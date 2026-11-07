"""Director conversation loop: one turn at a time per project.

Each turn streams the reply to the cockpit as ephemeral chat.delta events,
runs any tool calls through DirectorTools, sends the results back to the
Gateway, and persists the visible conversation in chat_messages.
"""

import asyncio
import json
import logging

import httpx

from ..db import Database, new_id, now
from ..events import EventBus
from .direct import ASK as DIRECT_ASK
from .direct import REFUSED as DIRECT_REFUSED
from .direct import SAVED as DIRECT_SAVED
from .direct import DirectError, DirectPlanner
from .gateway import GatewayClient, GatewayError, TurnResult
from .tools import TOOLS, DirectorTools

log = logging.getLogger("filmlab.director")
REMIND = ("You described {what} but {tool} didn't succeed, so nothing was saved. Call {tool} now with it; "
          "FilmLab writes nothing until that call succeeds. Pass its arguments directly (for example "
          '{{"scenes": [...]}}), not nested under the tool name.')


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

    def busy(self, project_id: str) -> bool:
        return project_id in self._active

    async def send(self, project_id: str, text: str, max_rounds: int | None = None,
                   expect: tuple[str, str] | None = None, parts: int = 0, resume=None) -> str:
        """max_rounds: more tool rounds for a turn known to need them (planning a storyboard).
        expect: (tool, what) for a turn that exists to make that tool succeed; if the director ends the turn
        without it succeeding, the chat says plainly that nothing was saved.
        parts: the expected tool may succeed up to this many times (a plan in parts); after each, FilmLab asks for
        the next part until the director says it's all planned.
        resume: async () -> str, the message that carries on a plan in parts from what is saved (as Plan more does).
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
        task = asyncio.create_task(self._run(project_id, turn_id, text, max_rounds, expect, parts, resume))
        self._tasks.add(task)
        self._task_of[project_id] = task
        task.add_done_callback(self._tasks.discard)
        return turn_id

    async def _run(self, project_id: str, turn_id: str, *args):
        try:
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
                    expect: tuple[str, str] | None = None, parts: int = 0, resume=None):
        project = await self.db.fetchone("SELECT title FROM projects WHERE id = ?", (project_id,))
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
        def opening(body: str) -> list[dict]:
            return [{"role": "user", "content": f"[FilmLab project: {project['title']}]\n{body}{notes}"}]

        messages = opening(text)

        async def on_delta(chunk):
            self.bus.publish_ephemeral("chat.delta", {"turn_id": turn_id, "text": chunk}, project_id)

        succeeded: set[str] = set()
        nudged = recovered = False
        first = messages
        self.tools.plan_parts[project_id] = asked = 0  # plan parts saved in this turn (only the first may replace)
        while True:
            try:
                for n in range(rounds):
                    result = await self.gateway.stream_turn(conversation, messages, TOOLS, on_delta, model=model)
                    if not result.tool_calls:
                        await self._save(project_id, "assistant", result.text)
                        await self.bus.publish("chat.message", {"turn_id": turn_id, "role": "assistant",
                                                                "content": result.text}, project_id)
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
                        done = self.tools.plan_parts.get(project_id, 0)
                        if (parts and asked < done < parts and n < rounds - 1
                                and "all planned" not in (result.text or "").lower()):
                            asked = done  # once per saved part: a reply without a new part ends the turn
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
            except (GatewayError, OSError) as exc:
                log.warning("director turn failed: %s", exc)
                if EMPTY_REPLY not in str(exc):
                    await self._fail(project_id, turn_id, f"director unavailable: {exc}")
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
                if refused > 2:
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
            if succeeded is not None and not failed(output) and "nothing changed" not in output.get("note", ""):
                succeeded.add(name)
            await self._save(project_id, "tool", content, tool_call_id=call["id"])
            follow.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        return follow

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
