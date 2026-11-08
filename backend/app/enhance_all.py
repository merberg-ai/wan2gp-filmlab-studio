"""Enhance all: the director enhances the owner's chosen shots one after another, each its own chat turn with the
same brief as the shot's ✨ Enhance (director/enhance.py), so each shows in the chat and its History as usual.
How far it got goes out as "enhance.progress" and with the project (project_extras), for the storyboard's bar."""

import asyncio
import logging

from . import shots
from .db import now
from .director.chat import ChatBusy
from .director.enhance import can_enhance, enhance_brief

log = logging.getLogger(__name__)


class EnhanceError(ValueError):
    pass


async def enhance_request(s, shot: dict) -> str:
    """The director's brief for one shot; its first Enhance keeps the director-drafted prompt as the owner's idea."""
    if not shot["idea"]:
        await s.db.execute("UPDATE shots SET idea = ? WHERE id = ?", (shot["prompt"], shot["id"]))
        shot = shot | {"idea": shot["prompt"]}
    scene = shot["scene_id"] and await s.db.fetchone("SELECT * FROM scenes WHERE id = ?", (shot["scene_id"],))
    project = await s.db.fetchone("SELECT brief, logline, style FROM projects WHERE id = ?", (shot["project_id"],))
    return enhance_brief(shot, await s.library.shot_refs(shot["id"]), await s.library.cast(shot["project_id"]),
                         dict(scene) if scene else None, (project and (project["brief"] or project["logline"])) or "",
                         (project and project["style"]) or "")


def why_not(shot: dict) -> str | None:
    """Why a shot can't be enhanced now, or None."""
    if shot["locked"]:
        return "locked"
    if not can_enhance(shot):
        return shot["status"]
    return None


class EnhanceAll:
    def __init__(self, studio):
        self.s = studio
        self.runs: dict[str, dict] = {}  # project_id -> the run's state, while it runs
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping: set[str] = set()

    def state(self, project_id: str) -> dict | None:
        return self.runs.get(project_id)

    async def start(self, project_id: str, shot_ids: list[str], actor: str) -> dict:
        if project_id in self.runs:
            raise EnhanceError("the director is already enhancing this project's shots")
        if self.s.chat.busy(project_id):
            raise EnhanceError("the director is still answering; try again when it's done")
        rows = await self.s.db.fetchall("SELECT * FROM shots WHERE project_id = ? AND deleted_at IS NULL ORDER BY position",
                                        (project_id,))
        wanted = set(shot_ids)
        todo = [r for r in rows if r["id"] in wanted and not why_not(r)]
        if not todo:
            raise EnhanceError("none of those shots can be enhanced (locked, approved or rendering)")
        state = {"started_at": now(), "total": len(todo), "done": 0, "failed": 0, "skipped": 0, "finished": 0,
                 "shot_ids": [r["id"] for r in todo], "current": None}
        self.runs[project_id] = state
        await self.s.db.audit(actor, "director.enhance_all", project_id, {"shots": state["shot_ids"]})
        task = asyncio.create_task(self._run(project_id, state))
        self._tasks[project_id] = task
        await self._publish(project_id)
        return state

    async def stop(self, project_id: str) -> bool:
        task = self._tasks.get(project_id)
        if not task or task.done():
            return False
        self._stopping.add(project_id)
        await self.s.chat.stop(project_id)  # the shot being enhanced now: what the director saved stays
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def shutdown(self):
        for t in list(self._tasks.values()):
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self, project_id: str, state: dict):
        try:
            for shot_id in state["shot_ids"]:
                await self._one(project_id, shot_id, state)
                state["finished"] += 1
                state["current"] = None
                await self._publish(project_id)
        except asyncio.CancelledError:
            if project_id not in self._stopping:
                raise
        except Exception:  # noqa: BLE001 - a run must never leave the bar up
            log.exception("enhance all failed in %s", project_id)
        finally:
            self._stopping.discard(project_id)
            self.runs.pop(project_id, None)
            self._tasks.pop(project_id, None)
            await self.s.bus.publish("enhance.done", {k: state[k] for k in ("total", "done", "failed", "skipped")},
                                     project_id)

    async def _one(self, project_id: str, shot_id: str, state: dict):
        try:
            shot = await shots.get_shot(self.s.db, shot_id)
        except shots.ShotError:
            state["skipped"] += 1
            return
        if shot.get("deleted_at") or why_not(shot):  # changed since the owner chose it
            state["skipped"] += 1
            return
        state["current"] = {"shot_id": shot_id, "position": shot["position"], "title": shot["title"]}
        await self._publish(project_id)
        before = shot["prompt"]
        brief = await enhance_request(self.s, shot)
        while True:  # the owner may be chatting with the director between two shots: wait for that turn
            await self.s.chat.wait(project_id)
            try:
                await self.s.chat.send(project_id, brief, expect=("update_shot", "the new prompt"))
                break
            except ChatBusy:
                await asyncio.sleep(0.5)
        await self.s.chat.wait(project_id)
        after = await self.s.db.fetchone("SELECT prompt FROM shots WHERE id = ?", (shot_id,))
        if after and after["prompt"] != before:
            state["done"] += 1
        else:
            state["failed"] += 1

    async def _publish(self, project_id: str):
        if project_id in self.runs:
            await self.s.bus.publish("enhance.progress", dict(self.runs[project_id]), project_id)
