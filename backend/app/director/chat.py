"""Director conversation loop: one turn at a time per project.

Each turn streams the reply to the cockpit as ephemeral chat.delta events,
runs any tool calls through DirectorTools, sends the results back to the
Gateway, and persists the visible conversation in chat_messages.
"""

import asyncio
import json
import logging

from ..db import Database, new_id, now
from ..events import EventBus
from .gateway import GatewayClient, GatewayError
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

    def busy(self, project_id: str) -> bool:
        return project_id in self._active

    async def send(self, project_id: str, text: str, max_rounds: int | None = None,
                   expect: tuple[str, str] | None = None, parts: int = 0) -> str:
        """max_rounds: more tool rounds for a turn known to need them (planning a storyboard).
        expect: (tool, what) for a turn that exists to make that tool succeed; if the director ends the turn
        without it succeeding, the chat says plainly that nothing was saved.
        parts: the expected tool may succeed up to this many times (a plan in parts); after each, FilmLab asks for
        the next part until the director says it's all planned."""
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
        task = asyncio.create_task(self._turn(project_id, turn_id, text, max_rounds, expect, parts))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda _: self._active.discard(project_id))
        task.add_done_callback(lambda _: self.tools.plan_parts.pop(project_id, None))
        return turn_id

    async def _turn(self, project_id: str, turn_id: str, text: str, rounds: int | None,
                    expect: tuple[str, str] | None = None, parts: int = 0):
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
        messages = [{"role": "user", "content": f"[FilmLab project: {project['title']}]\n{text}{notes}"}]

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
                await self._fail(project_id, turn_id, f"stopped after {rounds} tool rounds")
                return
            except (GatewayError, OSError) as exc:
                log.warning("director turn failed: %s", exc)
                if EMPTY_REPLY not in str(exc):
                    await self._fail(project_id, turn_id, f"director unavailable: {exc}")
                    return
                # OpenClaw's answer after the model's reply came back empty twice. Its session then tends to stay
                # stuck (every later turn comes back empty), so once per turn, when nothing was saved yet, FilmLab
                # starts a fresh conversation and sends the owner's message again.
                if parts and self.tools.plan_parts.get(project_id, 0):
                    await self._fail(project_id, turn_id, f"The director's reply came back empty after "
                                     f"{self.tools.plan_parts[project_id]} part(s) of the plan; those scenes are saved. "
                                     "Press Plan more to plan the rest.")
                    return
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
                                                 "ok": "error" not in output,
                                                 "summary": json.dumps(output)[:400]}, project_id)
            content = json.dumps(output)
            if succeeded is not None and "error" not in output and "nothing changed" not in output.get("note", ""):
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
