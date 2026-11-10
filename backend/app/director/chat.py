"""Director conversation loop: one turn at a time per project.

Each turn streams the reply to the cockpit as ephemeral chat.delta events,
runs any tool calls through DirectorTools, sends the results back to the
Gateway, and persists the visible conversation in chat_messages.
"""

import asyncio
import contextlib
import json
import logging

import httpx

from .. import coverage, shots
from ..db import Database, new_id, now
from ..events import EventBus
from ..storyboard import PLAN_OVERRUN, PLAN_SLACK_S
from .direct import ASK as DIRECT_ASK
from .direct import GAP_ASK as DIRECT_GAP_ASK
from .direct import REFUSED as DIRECT_REFUSED
from .direct import SAVED as DIRECT_SAVED
from .direct import DirectError, DirectPlanner
from .gateway import GatewayClient, GatewayError, TurnResult
from .planning import gap_ask, plan_brief
from .tools import TOOLS, DirectorTools

log = logging.getLogger("filmlab.director")
REMIND = ("You described {what} but {tool} didn't succeed, so nothing was saved. Call {tool} now with it; "
          "FilmLab writes nothing until that call succeeds. Pass its arguments directly (for example "
          '{{"scenes": [...]}}), not nested under the tool name.')
STYLE_CHARS = 600


def project_header(project: dict, body: str = "") -> str:
    """The line every turn starts with: the project, and its style so a chat edit or a new shot follows it too
    (Plan with director and Enhance already put the style in their own message)."""
    head = f"[FilmLab project: {project['title']}]"
    style = " ".join((project.get("style") or "").split())[:STYLE_CHARS]
    if style and "Style for this film" not in body:
        head += f"\n[Style for this film (follow it in every prompt you write): {style}]"
    if project.get("plan_library") == "none" and "Library: not used" not in body and "doesn't use the Library" not in body:
        head += ("\n[This film doesn't use the shared Library (a fresh start): no existing library assets as "
                 "references, and nothing borrowed from my other projects.]")
    if project.get("plan_library") == "cast" and "This film's cast" not in body:
        head += "\n[This film uses only its cast from the Library (list_assets shows them): no other assets.]"
    return head


# A plan is written in parts: one plan_storyboard call holding a whole long film is more output than the model
# manages in one go (it comes back empty), so it plans a scene or two per call and FilmLab asks for the next.
NEXT_PART = ("Part {n} is saved. The storyboard now has these scenes: {scenes}. If my brief has scenes you haven't "
             "planned yet, call plan_storyboard with the next one or two, in film order (don't set replace_drafts, don't "
             "repeat a scene, and keep the same style, characters and setting rules). If the whole brief is planned, "
             "reply starting with “All planned:” and a short summary.")


# OpenClaw's error when the model's reply is empty even after its own retry ("non_deliverable_terminal_turn").
EMPTY_REPLY = "'message': 'internal error', 'type': 'api_error'"


# Tools that only read: a turn that ran nothing else has saved nothing, so it can be retried safely.
READ_ONLY = {"get_project", "list_video_templates", "list_assets", "list_presets", "get_asset", "validate_shot",
             "get_render_status", "review_takes", "get_edit"}


def failed(output) -> bool:
    """A tool's result says it failed: an "error" with something in it (some results carry "error": null)."""
    return not isinstance(output, dict) or bool(output.get("error"))


class ChatBusy(RuntimeError):
    pass


class DirectorChat:
    def __init__(self, db: Database, bus: EventBus, gateway: GatewayClient, tools: DirectorTools, max_rounds: int,
                 settings=None):
        self.db, self.bus, self.gateway, self.tools = db, bus, gateway, tools
        self.max_rounds = max_rounds
        self.settings = settings  # DirectorSettings: the owner's notes, reply style, rounds, fresh conversations
        self._active: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._task_of: dict[str, asyncio.Task] = {}  # the project's running turn, for Stop
        self._stopping: set[str] = set()  # turns the owner stopped (a cancel otherwise means shutdown)
        self.planning: dict[str, dict] = {}  # a plan in parts being written: parts saved, seconds planned
        self.console = None  # Console, set by Studio: each tool call with its arguments and result
        self.gpu = None  # GpuShare (gpu_share.py), set by Studio: a local model waits for WanGP to finish

    def busy(self, project_id: str) -> bool:
        return project_id in self._active

    async def send(self, project_id: str, text: str, max_rounds: int | None = None,
                   expect: tuple[str, str] | None = None, parts: int = 0, resume=None,
                   tools: list[str] | None = None) -> str:
        """max_rounds: more tool rounds for a turn known to need them (planning a storyboard).
        expect: (tool, what) for a turn that exists to make that tool succeed; if the director ends the turn
        without it succeeding, the chat says plainly that nothing was saved.
        parts: the expected tool may succeed up to this many times (a plan in parts); after each, FilmLab asks for
        the next part until the director says it's all planned.
        resume: async () -> str, the message that carries on a plan in parts from what is saved (as Plan more does).
        tools: only these tools for this turn (a plan doesn't need renders or edits): fewer tokens, less to get wrong.
        If the reply comes back empty after some parts were saved, FilmLab sends it once in a fresh conversation."""
        if self.busy(project_id):
            raise ChatBusy("the director is still answering")
        self._active.add(project_id)  # claimed before any await, so concurrent sends cannot both pass
        try:
            turn_id = new_id()
            await self._save(project_id, "user", text)
            await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "user", "content": text}, project_id)
        except Exception:
            self._active.discard(project_id)
            raise
        if parts:
            self.planning[project_id] = {"turn_id": turn_id, "started_at": now(), "parts": 0, "seconds": 0.0,
                                         "scenes": 0}
        task = asyncio.create_task(self._run(project_id, turn_id, text, max_rounds, expect, parts, resume, tools))
        self._tasks.add(task)
        self._task_of[project_id] = task
        task.add_done_callback(self._tasks.discard)
        return turn_id

    async def _gpu_turn(self, project_id: str, turn_id: str):
        """While a LOCAL director model works, WanGP gets nothing new; it waits while WanGP renders (gpu_share.py)."""
        if not self.gpu:
            return contextlib.nullcontext()
        model = None
        if self.settings:
            try:
                model = (await self.settings.get()).get("model") or self.settings.model().get("model")
            except Exception:  # noqa: BLE001 - unknown model: treated as cloud, never held back
                model = None

        async def waiting(msg):
            await self._save(project_id, "note", msg)
            await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "note", "content": msg}, project_id)
        return self.gpu.llm(model, f"chat:{project_id}", waiting)

    async def _run(self, project_id: str, turn_id: str, *args):
        try:
            async with await self._gpu_turn(project_id, turn_id):
                await self._turn(project_id, turn_id, *args)
        except asyncio.CancelledError:
            if project_id not in self._stopping:
                raise  # the backend is shutting down
            saved = self.tools.plan_parts.get(project_id, 0)
            msg = ("Stopped by you" + (f" after {saved} part(s) of the plan; those scenes are saved. Plan more "
                                       "carries on from them." if saved else ". Anything the director saved before stays."))
            await self._save(project_id, "note", msg)
            await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "note", "content": msg}, project_id)
            await self.db.audit("owner", "director.stop", project_id, {"parts_saved": saved})
        finally:
            self._stopping.discard(project_id)
            self._active.discard(project_id)
            self._task_of.pop(project_id, None)
            self.tools.plan_parts.pop(project_id, None)
            self.planning.pop(project_id, None)
            await self.bus.publish("chat.done", {"turn_id": turn_id}, project_id)

    async def wait(self, project_id: str):
        """Until the project's director turn (if any) has finished."""
        while (task := self._task_of.get(project_id)) and not task.done():
            await asyncio.gather(asyncio.shield(task), return_exceptions=True)

    async def stop(self, project_id: str) -> bool:
        """Stop the project's director turn now. What it already saved stays; False if nothing was running."""
        task = self._task_of.get(project_id)
        if not task or task.done():
            return False
        self._stopping.add(project_id)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def _progress(self, project_id: str):
        """After a part of a plan was saved: how far it got, for the cockpit's progress bar."""
        state = self.planning.get(project_id)
        parts = self.tools.plan_parts.get(project_id, 0)
        if not state or parts == state["parts"]:
            return
        rows = await self.db.fetchall("SELECT frames FROM shots WHERE project_id = ? AND deleted_at IS NULL", (project_id,))
        scenes = await self.db.fetchone("SELECT COUNT(*) AS n FROM scenes WHERE project_id = ? AND deleted_at IS NULL",
                                        (project_id,))
        state.update(parts=parts, seconds=round(sum((r["frames"] - 1) / 24 for r in rows), 1), scenes=scenes["n"])
        await self.bus.publish("plan.progress", dict(state), project_id)

    async def _turn(self, project_id: str, turn_id: str, text: str, rounds: int | None,
                    expect: tuple[str, str] | None = None, parts: int = 0, resume=None, tools: list[str] | None = None):
        project = await self.db.fetchone("SELECT title, style, plan_library FROM projects WHERE id = ?", (project_id,))
        conversation, notes, model = f"filmlab:project:{project_id}", "", None
        try:
            if self.settings:
                owner = await self.settings.get()
                rounds = rounds or owner["chat_rounds"]
                conversation = await self.settings.conversation(project_id)
                notes = await self.settings.suffix()
                model = owner.get("model") or None
        except Exception:  # noqa: BLE001 - the owner's settings must never stop a turn
            log.exception("director settings unavailable; using defaults")
        rounds = rounds or self.max_rounds
        offered = [t for t in TOOLS if t["function"]["name"] in tools] if tools else TOOLS
        runs_on = model
        if not runs_on and self.settings:
            try:
                runs_on = self.settings.model().get("model")
            except Exception:  # noqa: BLE001
                runs_on = None
        # A local model's 32k context fills up over a long plan (each part's tool results stay in the
        # conversation) and the brief scrolls out of view: it then plans the rest from memory. Each part
        # after the first goes to a fresh conversation with the brief and what's saved (resume()).
        fresh_parts = bool(parts and resume and self.gpu and self.gpu.local(runs_on))
        if fresh_parts:
            # A local model planning with an earlier plan still in its conversation answers "it's already planned"
            # or re-uses that plan (live): every plan starts clean. The chat's own conversation is untouched.
            conversation = f"{conversation}:plan:{turn_id[:8]}"
        base_conversation = conversation

        def opening(body: str) -> list[dict]:
            return [{"role": "user", "content": f"{project_header(project, body)}\n{body}{notes}"}]

        messages = opening(text)

        async def gap_turn(ask: str):
            """(conversation, messages) that ask the director for a part of the brief the plan skips."""
            if not fresh_parts:
                return conversation, [{"role": "user", "content": ask + " When it's saved, reply with one line saying "
                                                                        "what you added."}]
            return f"{base_conversation}:gap{cover['asked']}", opening(await plan_brief(
                self.tools.studio, await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,)),
                replace=False, gap=ask))

        async def on_delta(chunk):
            self.bus.publish_ephemeral("chat.delta", {"turn_id": turn_id, "text": chunk}, project_id)

        succeeded: set[str] = set()
        nudged = recovered = challenged = False
        cover: dict = {}  # the brief-coverage check, once the director says the plan is done (see _cover)
        first = messages
        self.tools.plan_parts[project_id] = asked = 0  # plan parts saved in this turn (only the first may replace)
        while True:
            try:
                for n in range(rounds):
                    result = await self.gateway.stream_turn(conversation, messages, offered, on_delta, model=model)
                    if not result.tool_calls:
                        await self._save(project_id, "assistant", result.text)
                        await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "assistant",
                                                                "content": result.text}, project_id)
                        done = self.tools.plan_parts.get(project_id, 0)
                        claims_done = "all planned" in (result.text or "").lower()
                        short = (await self._short_of_target(project_id)
                                 if claims_done and parts and done and not challenged and not cover and n < rounds - 1
                                 else None)
                        wants_next = bool(parts and asked < done < parts and n < rounds - 1 and not claims_done
                                          and not cover)
                        if parts and (done or claims_done) and not short and not wants_next:
                            # The plan is done, says the director: is every part of my brief in it? (live: a battle
                            # between an entrance and an escape was skipped.) Each gap is asked for on its own.
                            ask = await self._cover(project_id, turn_id, cover, runs_on, n < rounds - 1)
                            if ask:
                                conversation, messages = await gap_turn(ask)
                                continue
                            if done or cover.get("checked"):
                                return  # "All planned" with nothing new to save is right when the brief is covered
                        if expect and expect[0] not in succeeded and not nudged and n < rounds - 1:
                            # Models sometimes describe the result instead of calling the tool: remind it once.
                            nudged = True
                            messages = [{"role": "user", "content": REMIND.format(tool=expect[0], what=expect[1])}]
                            await self._save(project_id, "note", "FilmLab reminded the director: " + messages[0]["content"])
                            continue
                        if expect and expect[0] not in succeeded:
                            await self._fail(project_id, turn_id, f"The director replied without saving {expect[1]} "
                                             f"({expect[0]} never succeeded), so nothing was written. Try again.")
                            return
                        if short:
                            # A small model says "All planned" after one scene (live: 23 s of a 60 s film, "21 shots"
                            # from memory). Short of the target, FilmLab asks once more.
                            challenged, asked = True, done
                            text_next = (f"The storyboard runs {short[0]:.0f} s of the {short[1]} s target, so my "
                                         "brief isn't all planned yet. ")
                            if fresh_parts:
                                conversation = f"{base_conversation}:part{done}b"
                                messages = opening(text_next + await resume())
                            else:
                                messages = [{"role": "user", "content": text_next + NEXT_PART.format(
                                    n=done, scenes="; ".join(f"“{t['title']}”" for t in
                                                             await self.tools.studio.storyboard.scenes(project_id)) or "none")}]
                            await self._save(project_id, "note", f"FilmLab asked the director to carry on: it said "
                                             f"all planned, but the storyboard runs {short[0]:.0f} s of {short[1]} s.")
                            continue
                        if wants_next:
                            asked = done  # once per saved part: a reply without a new part ends the turn
                            if fresh_parts:
                                conversation = f"{base_conversation}:part{done}"
                                messages = opening(await resume())
                                nudged = False
                                await self._save(project_id, "note", f"FilmLab asked the director for the next part "
                                                 f"(part {done} saved), in a fresh conversation with the brief and the "
                                                 "storyboard so far.")
                                continue
                            scenes = [s["title"] for s in await self.tools.studio.storyboard.scenes(project_id)]
                            messages = [{"role": "user", "content": NEXT_PART.format(
                                n=done, scenes="; ".join(f"“{t}”" for t in scenes) or "none")}]
                            await self._save(project_id, "note", f"FilmLab asked the director for the next part (part {done} saved).")
                            continue
                        return
                    messages = await self._run_tools(project_id, turn_id, result, succeeded)
                    if parts:
                        await self._progress(project_id)
                await self._fail(project_id, turn_id, f"stopped after {rounds} tool rounds")
                return
            except httpx.TimeoutException:
                # Live: the owner chose a 20 GB local model on a 16 GB GPU; it ran mostly on the CPU, nothing came
                # back for 5 minutes, and the chat said only "internal error: ReadTimeout".
                saved = self.tools.plan_parts.get(project_id, 0) if parts else 0
                local = bool(self.gpu and self.gpu.local(runs_on))
                log.warning("director turn timed out on %s", runs_on)
                await self._fail(project_id, turn_id, f"The director's model ({runs_on or 'OpenClaw default'}) didn't "
                                 "answer within 5 minutes, so FilmLab stopped waiting. "
                                 + ("A local model too big for this GPU's memory runs mostly on the CPU and is this "
                                    "slow: choose a smaller one in Settings → Director (it marks the ones that don't "
                                    "fit). " if local else "Try again; if it keeps happening, check Health. ")
                                 + (f"The {saved} part(s) of the plan already saved stay." if saved else
                                    "Nothing was saved." if not succeeded - READ_ONLY else "What was saved before stays."))
                return
            except (GatewayError, OSError, httpx.HTTPError) as exc:
                log.warning("director turn failed: %s", exc)
                if EMPTY_REPLY not in str(exc):
                    await self._fail(project_id, turn_id, f"director unavailable: {exc}")
                    return
                if cover.get("filling"):
                    # Empty while adding a skipped part of the brief: ask for just that part as JSON. (Live, this
                    # went on to the whole-plan fallback below, which planned the film a second time.)
                    try:
                        await self._gap_direct(project_id, turn_id, cover["filling"]["ask"], notes, runs_on, succeeded)
                    except Exception:  # noqa: BLE001 - the plan is saved; a gap that stays open is said in the chat
                        log.exception("asking for a skipped part directly failed in %s", project_id)
                    ask = await self._cover(project_id, turn_id, cover, runs_on, True)
                    if not ask:
                        return
                    conversation, messages = await gap_turn(ask)
                    continue
                if cover.get("checked"):  # the plan is saved and checked: nothing is planned again after that
                    await self._fail(project_id, turn_id, "The director's reply came back empty; the plan stays as saved.")
                    return
                # A plan's reply that came back empty is usually a plan_storyboard call Ollama's tool-call parser
                # dropped (gemma4 often), and asking again through OpenClaw mostly fails the same way: ask the model
                # for the plan as JSON instead (see direct.py).
                if (expect and expect[0] == "plan_storyboard"
                        and await self._plan_direct(project_id, turn_id, text, parts, resume, notes, rounds, model,
                                                    succeeded)):
                    return
                # OpenClaw's answer after the model's reply came back empty twice. Its session then tends to stay
                # stuck (every later turn comes back empty), so once per turn, when nothing was saved yet, FilmLab
                # starts a fresh conversation and sends the owner's message again.
                saved = self.tools.plan_parts.get(project_id, 0) if parts else 0
                if saved and (recovered or not self.settings or not resume):
                    await self._fail(project_id, turn_id, f"The director's reply came back empty after "
                                     f"{saved} part(s) of the plan; those scenes are saved. "
                                     "Press Plan more to plan the rest.")
                    return
                if saved:
                    # Parts are saved, so resending the owner's message would plan them again: carry on from the
                    # storyboard as it is now instead, as Plan more does, in a fresh conversation.
                    recovered, nudged = True, False
                    await self.settings.new_conversation(project_id)
                    conversation = await self.settings.conversation(project_id)
                    msg = (f"The director's reply came back empty after {saved} part(s) of the plan (those scenes are "
                           "saved), so FilmLab started a fresh director conversation and asked it to plan the rest.")
                    await self._save(project_id, "note", msg)
                    await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "note", "content": msg}, project_id)
                    await self.db.audit("system", "director.fresh_conversation", project_id,
                                        {"reason": "empty reply", "parts_saved": saved})
                    messages = opening(await resume())
                    continue
                if recovered or not self.settings or succeeded - READ_ONLY:
                    await self._fail(project_id, turn_id, "The director's reply came back empty"
                                     + (", so nothing more was saved" if succeeded - READ_ONLY else ", so nothing was saved")
                                     + ". Try again; if it keeps happening, press Start fresh first.")
                    return
                recovered, nudged = True, False
                await self.settings.new_conversation(project_id)
                conversation = await self.settings.conversation(project_id)
                msg = ("The director's reply came back empty, so FilmLab started a fresh director conversation and sent "
                       "your message again. Nothing had been saved.")
                await self._save(project_id, "note", msg)
                await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "note", "content": msg}, project_id)
                await self.db.audit("system", "director.fresh_conversation", project_id, {"reason": "empty reply"})
                messages = first
            except Exception as exc:  # never leave the cockpit waiting silently
                log.exception("director turn crashed")
                await self._fail(project_id, turn_id, f"internal error: {type(exc).__name__}")
                return

    async def _plan_direct(self, project_id, turn_id, text, parts, resume, notes, rounds, model, succeeded) -> bool:
        """Write the rest of the plan by asking the model on Ollama for each part as JSON, saving each through
        plan_storyboard as usual. False when the director's model isn't an Ollama model (nothing was done)."""
        if not self.settings:
            return False
        model = model or self.settings.model().get("model")
        if not model or not str(model).startswith("ollama/"):
            return False
        saved = self.tools.plan_parts.get(project_id, 0)
        msg = ("The director's reply came back empty: the model wrote its plan, but the tool call got lost on the way "
               "back from Ollama. FilmLab is asking the model for the plan directly instead"
               + (f", carrying on after the {saved} part(s) already saved." if saved else "."))
        await self._save(project_id, "note", msg)
        await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "note", "content": msg}, project_id)
        await self.db.audit("system", "director.direct_plan", project_id, {"parts_saved": saved, "model": model})
        planner = DirectPlanner(model, self.settings.ollama_transport)
        base = await resume() if saved and resume else text
        prompt, refused = f"{base}{notes}{DIRECT_ASK}", 0
        for _ in range(rounds):
            try:
                answer = await planner.ask(prompt)
            except (DirectError, httpx.HTTPError) as exc:
                done = self.tools.plan_parts.get(project_id, 0)
                await self._fail(project_id, turn_id, f"FilmLab couldn't get the plan from the model directly either "
                                 f"({exc})" + (f"; the {done} part(s) saved stay. Press Plan more to plan the rest."
                                               if done else ", so nothing was saved. Try again in a minute."))
                return True
            plan = answer.get("plan") if isinstance(answer.get("plan"), dict) else None
            if answer.get("all_planned") or not plan or not plan.get("scenes"):
                if not self.tools.plan_parts.get(project_id, 0):
                    await self._fail(project_id, turn_id, "The model answered without a plan, so nothing was saved. "
                                                          "Try again.")
                    return True
                summary = str(answer.get("summary") or "").strip() or "All planned."
                await self._save(project_id, "assistant", summary)
                await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "assistant", "content": summary},
                                       project_id)
                return True
            call = TurnResult("", [{"id": new_id()[:9], "name": "plan_storyboard", "arguments": json.dumps(plan)}])
            out = json.loads((await self._run_tools(project_id, turn_id, call, succeeded))[-1]["content"])
            if failed(out):
                refused += 1
                if refused > 3:
                    done = self.tools.plan_parts.get(project_id, 0)
                    await self._fail(project_id, turn_id, "The model's plan kept failing FilmLab's checks"
                                     + (f"; the {done} part(s) saved stay. Press Plan more to plan the rest."
                                        if done else ", so nothing was saved. Try again."))
                    return True
                prompt = DIRECT_REFUSED.format(errors=json.dumps(out.get("errors") or out["error"])[:4000])
                continue
            refused = 0
            await self._progress(project_id)
            done = self.tools.plan_parts.get(project_id, 0)
            if parts and done >= parts:
                await self._fail(project_id, turn_id, f"Planned {done} parts, the most one turn writes; press Plan "
                                                      "more to plan the rest.")
                return True
            scenes = [s["title"] for s in await self.tools.studio.storyboard.scenes(project_id)]
            prompt = DIRECT_SAVED.format(n=done, scenes="; ".join(f"“{t}”" for t in scenes) or "none")
        await self._fail(project_id, turn_id, f"stopped after {rounds} rounds")
        return True

    async def fresh(self, project_id: str, actor: str = "owner") -> dict:
        """Start a new director conversation for the project: it forgets the earlier chat; nothing else changes."""
        if self.busy(project_id):
            raise ChatBusy("the director is still answering; try again when it's done")
        n = await self.settings.new_conversation(project_id)
        msg = ("The director's memory of this chat was cleared: it starts fresh from here. Shots, scenes, takes and the "
               "edit are unchanged, and the messages above stay for you to read.")
        await self._save(project_id, "note", msg)
        await self.bus.publish("chat.message", {"turn_id": None, "role": "note", "content": msg}, project_id)
        await self.db.audit(actor, "director.fresh_conversation", project_id, {"conversation": n})
        return {"conversation": n}

    async def _short_of_target(self, project_id: str) -> tuple[float, int] | None:
        """(seconds planned, target) when the storyboard is well short of the film's target (under 60 %), else None."""
        p = await self.db.fetchone("SELECT target_seconds FROM projects WHERE id = ?", (project_id,))
        target = (p or {}).get("target_seconds")
        if not target:
            return None
        row = await self.db.fetchone("SELECT COALESCE(SUM(frames - 1), 0) / 24.0 AS s FROM shots WHERE project_id = ? "
                                     "AND deleted_at IS NULL", (project_id,))
        return (row["s"], target) if row["s"] < 0.6 * target else None

    async def _note(self, project_id: str, turn_id: str, msg: str):
        await self._save(project_id, "note", msg)
        await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "note", "content": msg}, project_id)

    async def _cover(self, project_id: str, turn_id: str, cover: dict, model: str | None, can_ask: bool) -> str | None:
        """The brief-coverage check (coverage.py), a step at a time: called whenever a planning turn would end.
        First it puts the shots the director just added for a gap in their place; the first time it compares the
        brief with the storyboard; then it returns what to ask the director for the next gap, or None when the
        turn can end. Trouble here never fails a plan that's saved."""
        try:
            return await self._cover_step(project_id, turn_id, cover, model, can_ask)
        except Exception:  # noqa: BLE001
            log.exception("brief coverage failed in %s", project_id)
            cover.update(gaps=[])
            cover.pop("filling", None)
            return None

    async def _cover_step(self, project_id, turn_id, cover, model, can_ask) -> str | None:
        studio = self.tools.studio

        def quoted(gaps):
            return "; ".join(f"“{b}”" for g in gaps for b in g["beats"])

        filling = cover.pop("filling", None)
        if filling:
            new = [s for s in await studio.storyboard.order(project_id) if s not in filling["shots"]]
            if new:
                after = filling["gap"].get("after")
                scenes = {s["id"] for s in await studio.storyboard.scenes(project_id)} - filling["scenes"]
                await studio.storyboard.place_after(project_id, new, after["id"] if after else None, scenes)
                rows = [await shots.get_shot(self.db, sid) for sid in new]
                await self._note(project_id, turn_id, "FilmLab put the added part of your brief in its place: "
                                 + ", ".join(f"shot {r['position']} “{r['title']}”"
                                             for r in sorted(rows, key=lambda r: r["position"])) + ".")
            else:
                await self._note(project_id, turn_id, "The director didn't add the skipped part of your brief "
                                 f"({quoted([filling['gap']])}). Add it yourself, or press Plan more.")
        if "gaps" not in cover:
            cover.update(gaps=[], asked=0)
            if not await coverage.applies(studio, project_id, model):
                return None
            await self._note(project_id, turn_id, "FilmLab is checking that every part of your brief is in the plan…")
            result = await coverage.check(studio, project_id, model)
            if result is None:
                await self._note(project_id, turn_id, "FilmLab couldn't compare the plan with your brief this time; "
                                                      "the plan stands as the director wrote it.")
                return None
            cover.update(checked=True, gaps=result["gaps"])
            if not result["gaps"]:
                await self._note(project_id, turn_id, f"Brief check: all {len(result['beats'])} parts of your brief "
                                                      "are in the storyboard.")
                return None
            await self._note(project_id, turn_id, f"Brief check: the plan skips {quoted(result['gaps'])}. FilmLab is "
                                                  "asking the director to add what's missing.")
        if not cover["gaps"]:
            return None
        p = await self.db.fetchone("SELECT target_seconds FROM projects WHERE id = ?", (project_id,))
        row = await self.db.fetchone("SELECT COALESCE(SUM(frames - 1), 0) / 24.0 AS s FROM shots WHERE project_id = ? "
                                     "AND deleted_at IS NULL", (project_id,))
        target = (p or {}).get("target_seconds")
        room = target * PLAN_OVERRUN + PLAN_SLACK_S - row["s"] if target else 10.0
        stop = None
        if room < 4:
            stop = (f"The storyboard already runs {row['s']:.0f} s of the {target} s target, so FilmLab didn't ask for "
                    "more. Still skipped from your brief: {}. Shorten other shots or raise the target, then press "
                    "Plan more.")
        elif not can_ask or cover["asked"] >= coverage.MAX_GAPS:
            stop = "Still skipped from your brief: {}. Press Plan more to add them."
        if stop:
            left, cover["gaps"] = cover["gaps"], []
            await self._note(project_id, turn_id, stop.format(quoted(left)))
            return None
        gap = cover["gaps"].pop(0)
        if gap.get("after"):  # an earlier gap's shots may have moved it
            gap["after"] = await shots.get_shot(self.db, gap["after"]["id"])
        cover["asked"] += 1
        ask = gap_ask(gap, min(8.0, room - 1, 3.0 + 2 * len(gap["beats"])))
        cover["filling"] = {"gap": gap, "ask": ask, "shots": set(await studio.storyboard.order(project_id)),
                            "scenes": {s["id"] for s in await studio.storyboard.scenes(project_id)}}
        return ask

    async def _gap_direct(self, project_id, turn_id, ask: str, notes: str, model: str | None, succeeded: set):
        """The skipped part of the brief, asked of the model on Ollama as JSON (see direct.py) and saved through
        plan_storyboard as usual. _cover then finds the new shots, or says that nothing was added."""
        if not self.settings or not model or not str(model).startswith("ollama/"):
            return
        await self._note(project_id, turn_id, "The director's reply came back empty, so FilmLab is asking the model "
                                              "for the skipped part directly.")
        project = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        planner = DirectPlanner(model, self.settings.ollama_transport)
        prompt = f"{await plan_brief(self.tools.studio, project, replace=False, gap=ask)}{notes}{DIRECT_GAP_ASK}"
        for _ in range(3):
            try:
                answer = await planner.ask(prompt)
            except (DirectError, httpx.HTTPError) as exc:
                log.warning("direct gap plan failed: %s", exc)
                return
            plan = answer.get("plan") if isinstance(answer.get("plan"), dict) else None
            if not plan or not plan.get("scenes"):
                return
            call = TurnResult("", [{"id": new_id()[:9], "name": "plan_storyboard", "arguments": json.dumps(plan)}])
            out = json.loads((await self._run_tools(project_id, turn_id, call, succeeded))[-1]["content"])
            if not failed(out):
                return
            prompt = DIRECT_REFUSED.format(errors=json.dumps(out.get("errors") or out["error"])[:4000])

    async def _run_tools(self, project_id, turn_id, result, succeeded: set | None = None) -> list[dict]:
        """Execute the turn's tool calls; return the follow-up messages for the Gateway."""
        calls = [{"id": c["id"] or new_id()[:9], "type": "function",
                  "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                 for c in result.tool_calls]
        await self._save(project_id, "assistant", result.text or "", tool_calls=calls)
        follow = [{"role": "assistant", "content": result.text or "", "tool_calls": calls}]
        for call in calls:
            name, args = call["function"]["name"], call["function"]["arguments"]
            output = await self.tools.run(project_id, name, args)
            await self.db.audit("director", f"tool.{name}", project_id, {"args": args[:500]})
            await self.bus.publish("chat.tool", {"turn_id": turn_id, "name": name, "arguments": args,
                                                 "ok": not failed(output),
                                                 "summary": json.dumps(output)[:400]}, project_id)
            content = json.dumps(output)
            if self.console:
                await self._console_tool(project_id, name, args, output)
            if succeeded is not None and not failed(output) and "nothing changed" not in output.get("note", ""):
                succeeded.add(name)
            await self._save(project_id, "tool", content, tool_call_id=call["id"])
            follow.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        return follow

    async def _console_tool(self, project_id, name, args, output):
        try:
            parsed = json.loads(args or "{}")
        except ValueError:
            parsed = args
        bad = failed(output)
        if bad:
            errs = output.get("errors") if isinstance(output, dict) else None
            msg = f"{name} was refused: {'; '.join(errs[:2]) if errs else output.get('error') if isinstance(output, dict) else output}"
        elif name == "plan_storyboard" and isinstance(output, dict):
            msg = (f"plan_storyboard saved {output.get('created_scenes', 0)} scene(s), "
                   f"{len(output.get('created_shots') or [])} shot(s)")
        else:
            msg = f"{name} done"
        fixes = output.get("fixed_by_filmlab") if isinstance(output, dict) else None
        if fixes:
            msg += f" · FilmLab repaired {len(fixes)} thing(s)"
        source = "Plan" if name == "plan_storyboard" else "Director"
        await self.console.add("error" if bad else "info", source, msg, project_id, {"tool": name, "arguments": parsed,
                                                                                   "result": output})

    async def _fail(self, project_id, turn_id, message):
        await self._save(project_id, "error", message)
        await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "error", "content": message}, project_id)

    async def _save(self, project_id, role, content, tool_calls=None, tool_call_id=None):
        await self.db.execute(
            "INSERT INTO chat_messages (project_id, role, content, tool_calls, tool_call_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, role, content, json.dumps(tool_calls) if tool_calls else None, tool_call_id, now()))

    async def shutdown(self):
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
