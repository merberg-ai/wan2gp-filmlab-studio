"""Storyboard batches: owner-approved shots rendered one after another, in storyboard order.

A batch lives in the database, so it survives a backend restart and a closed browser. It never keeps
more than queue_cap FilmLab jobs in WanGP's queue (default 1: WanGP renders one at a time, and the
queue stays free for the owner's own UI jobs). A failed shot is left for Retry and the batch goes on.
Pause lets the current render finish and submits nothing new; Cancel also cancels the current render.
Each shot is approved and rendered exactly like a single render (same checks, provenance, errors).
"""

import asyncio
import logging

from . import shots
from .db import Database, new_id, now
from .events import EventBus
from .renders import ACTIVE, RenderError

log = logging.getLogger("filmlab.batches")
RENDERABLE = ("draft", "pending_approval", "failed", "ready", "approved")
OPEN = ("running", "paused")


class BatchError(ValueError):
    pass


class Batches:
    def __init__(self, db: Database, bus: EventBus, renders, storyboard):
        self.db, self.bus, self.renders, self.storyboard = db, bus, renders, storyboard
        self._lock = asyncio.Lock()  # one pump at a time: no shot is ever submitted twice
        self.closing = False  # set when the backend shuts down: the next process submits the next shot
        renders.on_finish = self.job_finished

    # --- reading ---------------------------------------------------------

    async def get(self, batch_id: str) -> dict:
        b = await self.db.fetchone("SELECT * FROM batches WHERE id = ?", (batch_id,))
        if not b:
            raise BatchError("no such batch")
        b["items"] = await self.db.fetchall(
            "SELECT i.*, s.title, s.position AS shot_position FROM batch_items i JOIN shots s ON s.id = i.shot_id "
            "WHERE i.batch_id = ? ORDER BY i.position", (batch_id,))
        return b

    async def latest(self, project_id: str) -> dict | None:
        row = await self.db.fetchone("SELECT id FROM batches WHERE project_id = ? ORDER BY "
                                     "CASE WHEN status IN ('running', 'paused') THEN 0 ELSE 1 END, created_at DESC LIMIT 1",
                                     (project_id,))
        return await self.get(row["id"]) if row else None

    # --- start -----------------------------------------------------------

    async def create(self, project_id: str, shot_ids: list[str], actor: str, queue_cap: int = 1) -> dict:
        if await self.db.fetchone("SELECT 1 FROM batches WHERE project_id = ? AND status IN ('running', 'paused')",
                                  (project_id,)):
            raise BatchError("this project already has a batch running or paused; finish or cancel it first")
        if not shot_ids:
            raise BatchError("choose at least one shot")
        order = await self.storyboard.order(project_id)
        unknown = [s for s in shot_ids if s not in order]
        if unknown:
            raise BatchError("some shots aren't in this project (or were deleted)")
        picked = [s for s in order if s in set(shot_ids)]  # always storyboard order, whatever order was sent
        for sid in picked:
            shot = await shots.get_shot(self.db, sid)
            if shot["locked"]:
                raise BatchError(f"shot {shot['position']} “{shot['title']}” is locked; unlock it to render it again")
            if shot["status"] not in RENDERABLE:
                raise BatchError(f"shot {shot['position']} “{shot['title']}” is {shot['status']}")
        bid, ts = new_id(), now()

        def write(c):
            c.execute("INSERT INTO batches (id, project_id, status, queue_cap, requested_by, created_at, updated_at) "
                      "VALUES (?, ?, 'running', ?, ?, ?, ?)", (bid, project_id, max(1, min(int(queue_cap), 4)), actor, ts, ts))
            for i, sid in enumerate(picked, start=1):
                c.execute("INSERT INTO batch_items (batch_id, position, shot_id, updated_at) VALUES (?, ?, ?, ?)",
                          (bid, i, sid, ts))
        await self.db.transaction(write)
        await self.db.audit(actor, "batch.create", bid, {"shots": picked})
        await self._changed(project_id, bid)
        await self.pump(bid)
        return await self.get(bid)

    # --- the runner --------------------------------------------------------

    async def pump(self, batch_id: str):
        """Submit pending shots until the queue cap is reached; finish the batch when nothing is left."""
        async with self._lock:
            b = await self.db.fetchone("SELECT * FROM batches WHERE id = ?", (batch_id,))
            if not b:
                return
            while True:
                b = await self.db.fetchone("SELECT * FROM batches WHERE id = ?", (batch_id,))
                if b["status"] != "running" or self.closing:
                    break
                rendering = (await self.db.fetchone("SELECT COUNT(*) AS n FROM batch_items WHERE batch_id = ? "
                                                    "AND status = 'rendering'", (batch_id,)))["n"]
                nxt = await self.db.fetchone("SELECT * FROM batch_items WHERE batch_id = ? AND status = 'pending' "
                                             "ORDER BY position LIMIT 1", (batch_id,))
                if not nxt:
                    if not rendering:
                        await self.db.execute("UPDATE batches SET status = 'done', finished_at = ?, updated_at = ? "
                                              "WHERE id = ?", (now(), now(), batch_id))
                        await self.db.audit("system", "batch.done", batch_id)
                    break
                if rendering >= b["queue_cap"]:
                    break
                if self.renders.gate and (held := await self.renders.gate()):
                    await self._set_status(batch_id, "paused", held)
                    break
                await self._submit(b, nxt)
        await self._changed(b["project_id"], batch_id)

    async def _submit(self, b: dict, item: dict):
        def mark(status, job_id=None, error=None):
            return self.db.execute("UPDATE batch_items SET status = ?, job_id = COALESCE(?, job_id), error = ?, "
                                   "updated_at = ? WHERE batch_id = ? AND position = ?",
                                   (status, job_id, error, now(), b["id"], item["position"]))
        try:
            shot = await shots.get_shot(self.db, item["shot_id"])
        except shots.ShotError:
            return await mark("skipped", error="the shot was deleted")
        if shot["locked"]:
            return await mark("skipped", error="the shot was locked after the batch started")
        if shot["status"] not in RENDERABLE:
            return await mark("skipped", error=f"the shot is {shot['status']}")
        try:
            approved = await shots.approve(self.db, self.bus, shot, "owner")
            job = await self.renders.submit(approved, "owner")
        except (RenderError, shots.ShotError) as exc:
            await shots.undo_approve(self.db, self.bus, shot, "owner")
            log.info("batch %s: shot %s not rendered: %s", b["id"], shot["id"], exc)
            return await mark("failed", error=str(exc)[:500])
        await self.db.audit("owner", "batch.render", b["id"], {"shot_id": shot["id"], "job_id": job["id"]})
        await mark("rendering", job_id=job["id"])
        current = await self.renders.job(job["id"])
        if current["status"] not in ACTIVE:  # it ended before we marked it: settle it here
            await self._settle(item | {"status": "rendering"}, b, current["status"])

    async def job_finished(self, job: dict, status: str):
        """Called by the render service when any render ends."""
        item = await self.db.fetchone("SELECT * FROM batch_items WHERE job_id = ? AND status = 'rendering'", (job["id"],))
        if not item:
            return
        b = await self.db.fetchone("SELECT * FROM batches WHERE id = ?", (item["batch_id"],))
        if await self._settle(item, b, status):
            await self.pump(b["id"])
        await self._changed(b["project_id"], b["id"])

    async def _settle(self, item: dict, b: dict, status: str) -> bool:
        """Record how an item's render ended (once). True if it was still open."""
        result = {"done": "done", "cancelled": "cancelled"}.get(status, "failed")
        error = None if result == "done" else (await self.renders.job(item["job_id"])).get("error") or status
        n = await self.db.run(lambda c: c.execute(
            "UPDATE batch_items SET status = ?, error = ?, updated_at = ? WHERE batch_id = ? AND position = ? "
            "AND status = 'rendering'", (result, error, now(), b["id"], item["position"])).rowcount)
        if n and status == "interrupted" and b["status"] == "running":
            # WanGP lost the render (e.g. it restarted): don't guess; stop and let the owner decide.
            await self._set_status(b["id"], "paused", "WanGP lost the current render; Retry that shot or Resume")
        return bool(n)

    async def resume_all(self):
        """After a backend restart: settle items whose job already ended, then carry on."""
        for b in await self.db.fetchall("SELECT * FROM batches WHERE status IN ('running', 'paused')"):
            for item in await self.db.fetchall("SELECT * FROM batch_items WHERE batch_id = ? AND status = 'rendering'",
                                               (b["id"],)):
                job = await self.renders.job(item["job_id"])
                if job["status"] in ("done", "failed", "cancelled", "interrupted"):
                    await self.job_finished(job, job["status"])
            await self.pump(b["id"])

    # --- owner controls -------------------------------------------------------

    async def pause(self, batch_id: str, actor: str) -> dict:
        b = await self.get(batch_id)
        if b["status"] != "running":
            raise BatchError(f"the batch is {b['status']}")
        await self._set_status(batch_id, "paused", "paused by the owner")
        await self.db.audit(actor, "batch.pause", batch_id)
        await self._changed(b["project_id"], batch_id)
        return await self.get(batch_id)

    async def resume(self, batch_id: str, actor: str) -> dict:
        b = await self.get(batch_id)
        if b["status"] != "paused":
            raise BatchError(f"the batch is {b['status']}")
        await self._set_status(batch_id, "running", None)
        await self.db.audit(actor, "batch.resume", batch_id)
        await self.pump(batch_id)
        return await self.get(batch_id)

    async def cancel(self, batch_id: str, actor: str) -> dict:
        b = await self.get(batch_id)
        if b["status"] not in OPEN:
            raise BatchError(f"the batch is {b['status']}")
        await self.db.execute("UPDATE batches SET status = 'cancelled', finished_at = ?, updated_at = ? WHERE id = ?",
                              (now(), now(), batch_id))
        await self.db.execute("UPDATE batch_items SET status = 'cancelled', updated_at = ? WHERE batch_id = ? "
                              "AND status = 'pending'", (now(), batch_id))
        for item in b["items"]:
            if item["status"] == "rendering" and item["job_id"]:
                try:
                    await self.renders.cancel(item["job_id"], actor)
                except RenderError as exc:  # it finished in the meantime
                    log.info("batch %s: cancel of %s: %s", batch_id, item["job_id"], exc)
        await self.db.audit(actor, "batch.cancel", batch_id)
        await self._changed(b["project_id"], batch_id)
        return await self.get(batch_id)

    async def retry(self, batch_id: str, position: int, actor: str) -> dict:
        b = await self.get(batch_id)
        item = next((i for i in b["items"] if i["position"] == int(position)), None)
        if not item or item["status"] not in ("failed", "cancelled", "skipped"):
            raise BatchError("only a failed, cancelled or skipped shot can be retried")
        if b["status"] == "cancelled":
            raise BatchError("the batch was cancelled; start a new one")
        await self.db.execute("UPDATE batch_items SET status = 'pending', error = NULL, updated_at = ? WHERE batch_id = ? "
                              "AND position = ?", (now(), batch_id, item["position"]))
        if b["status"] == "done":
            await self.db.execute("UPDATE batches SET status = 'running', finished_at = NULL WHERE id = ?", (batch_id,))
        await self.db.audit(actor, "batch.retry", batch_id, {"shot_id": item["shot_id"]})
        await self.pump(batch_id)
        return await self.get(batch_id)

    async def _set_status(self, batch_id: str, status: str, reason: str | None):
        await self.db.execute("UPDATE batches SET status = ?, pause_reason = ?, updated_at = ? WHERE id = ?",
                              (status, reason, now(), batch_id))

    async def _changed(self, project_id: str, batch_id: str):
        await self.bus.publish("batch.updated", {"batch_id": batch_id}, project_id)
