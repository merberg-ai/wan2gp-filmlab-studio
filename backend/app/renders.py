"""Render orchestration: approved shot -> WanGP job -> ingested take.

Rules learned probing WanGP's MCP server:
- submit with wait=false and a stable request_id (idempotent per agent context);
- progress is phase + current_step/total_steps; never invent a percentage;
- after a WanGP restart get_job may say interrupted/recovered while WanGP's own
  queue re-runs the task, so reconcile against list_queue and the gallery by
  client_id before declaring failure. A done get_job has no client_ids (only
  gallery_items[].task_id); WanGP names an MCP job's tasks mcp_<job_id>_<n>, which
  is what list_queue's client_id and the gallery's task_id carry. A job reported
  interrupted during shutdown is restored only after WanGP restarts, hence the
  RESTART_GRACE_S window.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .db import Database, new_id, now
from .events import EventBus
from . import sound
from .media import probe, sha256_file, thumbnail
from .wangp import errors
from .wangp.client import WanGPClient, WanGPError

log = logging.getLogger("filmlab.renders")

ACTIVE = ("submitting", "queued", "running", "cancelling", "restored")
TERMINAL_WANGP = ("done", "error", "interrupted", "cancelled", "unknown_after_restart")
POLL_WAIT_S = 10
GALLERY_SCAN = 60
# A job reported interrupted while WanGP shuts down is only re-queued once WanGP is
# back up and has restored its saved queue (minutes, with model loading). Keep looking
# for that long before declaring the take lost; time spent unreachable doesn't count.
RESTART_GRACE_S = 600
# A running job whose phase/step hasn't moved for this long is flagged (never cancelled).
STALL_S = 600


def queue_view(queue: dict, prefix: str | None, agent_context: str | None) -> dict:
    """Where a FilmLab task stands in WanGP's shared queue, and who owns what's running.

    list_queue includes WanGP UI tasks; FilmLab's carry its mcp_agent_id and client_ids
    mcp_<job_id>_N. Returns ahead (tasks before ours, None if ours isn't listed) and the
    owner of the running task: "this" (our job), "filmlab", "agent" or "ui".
    """
    tasks = sorted(queue.get("tasks", []), key=lambda t: t.get("position") or 0)

    def owner(t):
        if prefix and (t.get("client_id") or "").startswith(prefix):
            return "this"
        if t.get("mcp_agent_id") and t.get("mcp_agent_id") == agent_context:
            return "filmlab"
        return "agent" if t.get("mcp_agent_id") or t.get("client_id") else "ui"

    idx = next((i for i, t in enumerate(tasks) if owner(t) == "this"), None)
    running = next((t for t in tasks if t.get("status") == "running"), None)
    return {"ahead": idx, "total": len(tasks), "running": owner(running) if running else None}


def summarize_queue(queue: dict, agent_context: str | None) -> dict:
    """Queue totals split into FilmLab's tasks and everyone else's (WanGP UI, other agents)."""
    tasks = queue.get("tasks", [])
    ours = sum(1 for t in tasks if t.get("mcp_agent_id") and t.get("mcp_agent_id") == agent_context)
    return {"total": len(tasks), "filmlab": ours, "other": len(tasks) - ours,
            "running": sum(1 for t in tasks if t.get("status") == "running")}


def _age_s(ts: str | None) -> float:
    if not ts:
        return 0.0
    then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - then).total_seconds()


class RenderError(RuntimeError):
    pass


class UnknownTemplate(RenderError, ValueError):
    pass


class RenderService:
    """Video renders of shots. ImageJobs (images.py) reuses this lifecycle for reference images."""
    TABLE = "render_jobs"
    EVENT = "render"
    GALLERY_TYPE = "video"

    def __init__(self, db: Database, bus: EventBus, wangp: WanGPClient, media_dir: Path, workspace: str):
        self.db, self.bus, self.wangp = db, bus, wangp
        self.media_dir = media_dir
        self.workspace_name = workspace
        self._tasks: dict[str, asyncio.Task] = {}
        self._templates: list[dict] | None = None
        self.workspace: dict | None = None
        self.on_finish = None  # async (job, status) -> None, set by the batch runner
        self.listeners: list = []  # more async (job, status) -> None: redo runs, take inspection
        self.gate = None  # async () -> reason | None: the owner's GPU hold, set by Studio

    # --- setup -----------------------------------------------------------

    async def connect(self):
        saved = await self.db.kv_get("wangp.agent_context")
        self.workspace = await self.wangp.ensure_workspace(self.workspace_name, saved)
        if self.workspace["agent_context"] != saved:
            await self.db.kv_set("wangp.agent_context", self.workspace["agent_context"])
        log.info("WanGP workspace %s (%s)", self.workspace.get("name"), self.workspace.get("workspace_id"))

    def gallery_type(self, job: dict) -> str:
        return self.GALLERY_TYPE

    async def resume_all(self):
        for job in await self.db.fetchall(f"SELECT id FROM {self.TABLE} WHERE status IN {ACTIVE}"):
            self._spawn(job["id"])

    async def shutdown(self):
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # --- templates / settings -------------------------------------------

    async def video_templates(self) -> list[dict]:
        """Video templates from both WanGP categories: plain (gen_video) and reference (gen_video_with_refs)."""
        if self._templates is None:
            templates = []
            for task in ("gen_video", "gen_video_with_refs"):
                res = await self.wangp.call("wangp_templates", {"task": task})
                templates += [
                    {"template": t["template"], "model_type": t["model_type"], "task": task,
                     "default": t.get("default", False) and task == "gen_video",
                     "image_inputs": (t.get("media_inputs") or {}).get("image", []),
                     "has_audio": "audio" in (t.get("media_inputs") or {})}
                    for t in res.get("templates", [])]
            self._templates = templates
        return self._templates

    async def default_template(self) -> dict:
        templates = await self.video_templates()
        return next((t for t in templates if t["default"]), templates[0])

    async def resolve_template(self, name: str | None) -> dict:
        templates = await self.video_templates()
        if not name:
            return await self.default_template()
        match = next((t for t in templates if t["template"] == name), None)
        if not match:
            raise UnknownTemplate(f"unknown template {name!r}; installed: {[t['template'] for t in templates]}")
        return match

    async def build_settings(self, shot: dict, refs: list[dict] | None = None) -> dict:
        tpl = await self.resolve_template(shot["template"])
        full = await self.wangp.call("wangp_templates", {"task": tpl.get("task", "gen_video"), "template": tpl["template"]})
        settings = dict(full.get("settings") or {})
        settings.update({
            "model_type": tpl["model_type"], "prompt": shot["prompt"],
            "resolution": shot["resolution"], "video_length": shot["frames"], "seed": shot["seed"],
        })
        if refs:  # ordered: position N is <Picture N> in the prompt
            settings["image_refs"] = [r["wangp_media_id"] for r in refs]
        sound_fields = sound.load(shot.get("sound"))
        if sound_fields is not None and sound.is_h3(tpl["model_type"]):
            # H3 voices exactly what its structured prompt says: FilmLab writes that prompt from the shot's fields
            settings = sound.apply_h3(settings, shot["prompt"], sound_fields, refs or [], shot.get("_voices") or [],
                                      shot.get("idea") or shot.get("title") or "")
        elif sound_fields is not None and tpl.get("has_audio"):
            settings["prompt"] = sound.plain_prompt(shot["prompt"], sound_fields)
        return settings

    # Wired by Studio: shot -> (effective template, uploaded refs, blocking issues). Defaults keep tests of
    # this module standalone.
    async def prepare_shot(self, shot: dict) -> tuple[dict, list[dict], list[dict]]:
        return shot, [], []

    async def reupload_refs(self, refs: list[dict]) -> list[dict]:
        return refs

    async def reupload_voices(self, shot: dict) -> dict:
        return shot

    async def validate(self, shot: dict) -> dict:
        shot, refs, issues = await self.prepare_shot(shot)
        res = await self.wangp.call("wangp_validate", {"settings": await self.build_settings(shot, refs)})
        task = (res.get("tasks") or [{}])[0]
        return {
            "valid": res.get("valid", False),
            "diagnostics": res.get("diagnostics", []),
            "canvas": (task.get("sizing") or {}).get("canvas_dimensions"),
            "requested_video": task.get("requested_video"),
            "windows": len((task.get("window_plan") or {}).get("windows", [])),
            "available": (task.get("availability") or {}).get("available"),
            "queue": res.get("queue"),
            "template": shot["template"], "refs": len(refs), "issues": issues,
        }

    # --- lifecycle -------------------------------------------------------

    async def submit(self, shot: dict, actor: str) -> dict:
        if self.gate and (held := await self.gate()):
            raise RenderError(held)
        if shot["status"] != "approved":
            raise RenderError(f"shot is {shot['status']}; only approved shots can render")
        active = await self.db.fetchone(f"SELECT id FROM render_jobs WHERE shot_id = ? AND status IN {ACTIVE}", (shot["id"],))
        if active:
            raise RenderError("this shot already has an active render")
        shot, refs, issues = await self.prepare_shot(shot)
        blocking = [i["message"] for i in issues if i["level"] == "block"]
        if blocking:
            raise RenderError("Can't render this shot: " + "; ".join(blocking))
        settings = await self.build_settings(shot, refs)
        job_id = new_id()
        request_id = f"filmlab-{job_id}"
        await self.db.execute(
            "INSERT INTO render_jobs (id, project_id, shot_id, request_id, settings, refs, status, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'submitting', ?)",
            (job_id, shot["project_id"], shot["id"], request_id, json.dumps(settings), json.dumps(refs), now()))
        await self._set_shot(shot, "queued")
        await self.db.audit(actor, "render.submit", shot["id"], {"job_id": job_id})
        try:
            try:
                await self._submit_to_wangp(job_id)
            except WanGPError as exc:
                if not (refs or shot.get("_voices")) or "Unknown WanGP media_id" not in str(exc):
                    raise
                # WanGP lost a cached reference upload (e.g. its gallery was cleared): upload again, once.
                log.warning("job %s: WanGP lost a reference upload; uploading again", job_id)
                refs = await self.reupload_refs(refs)
                shot = await self.reupload_voices(shot)
                settings = await self.build_settings(shot, refs)
                # A fresh request_id: WanGP may have recorded the rejected request under the old one.
                await self.db.execute("UPDATE render_jobs SET settings = ?, refs = ?, request_id = ? WHERE id = ?",
                                      (json.dumps(settings), json.dumps(refs), f"filmlab-{job_id}-r1", job_id))
                await self._submit_to_wangp(job_id)
        except httpx.HTTPError as exc:
            # The request may or may not have reached WanGP; the follower resubmits with the same
            # request_id, which WanGP deduplicates, so this can't create a second render.
            log.warning("submit of %s hit %s; retrying with the same request_id", job_id, type(exc).__name__)
            self._spawn(job_id)
            return await self.job(job_id)
        except WanGPError as exc:
            await self._finish(job_id, "failed", error=str(exc))
            info = errors.describe(errors.classify(str(exc)))
            raise RenderError(f"{info['message']} {info['hint']} (WanGP: {exc})") from exc
        self._spawn(job_id)
        return await self.job(job_id)

    async def _submit_to_wangp(self, job_id: str):
        job = await self.job(job_id)
        res = await self.wangp.call("wangp_generate", {
            "settings": json.loads(job["settings"]), "wait": False, "request_id": job["request_id"]})
        await self.db.execute(f"UPDATE {self.TABLE} SET wangp_job_id = ?, status = 'queued' WHERE id = ?",
                              (res["job_id"], job_id))
        await self._emit(job_id, "render.submitted", canvas=res.get("canvas"), reused=res.get("reused"))

    async def cancel(self, job_id: str, actor: str) -> dict:
        job = await self.job(job_id)
        if job["status"] not in ACTIVE:
            raise RenderError(f"job is {job['status']}")
        await self.db.execute(f"UPDATE {self.TABLE} SET status = 'cancelling' WHERE id = ?", (job_id,))
        await self.db.audit(actor, "render.cancel", job_id, {"restored": job["status"] == "restored"})
        # A restored job's WanGP record already says interrupted, so cancel_job can't reach the
        # task WanGP re-queued after its restart; _reconcile removes that via the guarded queue cancel.
        if job["status"] != "restored" and job["wangp_job_id"]:
            await self.wangp.session("cancel_job", job_id=job["wangp_job_id"])
        await self._emit(job_id, "render.progress")
        return await self.job(job_id)

    def _spawn(self, job_id: str):
        if job_id in self._tasks and not self._tasks[job_id].done():
            return
        self._tasks[job_id] = asyncio.create_task(self._follow(job_id), name=f"render-{job_id}")

    async def _follow(self, job_id: str):
        try:
            job = await self.job(job_id)
            if job["status"] == "submitting":  # backend died or the network failed mid-submit
                await self._submit_to_wangp(job_id)  # same request_id: WanGP returns the same job
                job = await self.job(job_id)
            if job["status"] == "restored":
                return await self._reconcile(job_id)
            prefix = f"mcp_{job['wangp_job_id']}_" if job["wangp_job_id"] else None
            while True:
                job = await self.job(job_id)
                snap = await self.wangp.session("get_job", job_id=job["wangp_job_id"], wait_s=POLL_WAIT_S)
                status = snap.get("status")
                if status in TERMINAL_WANGP or snap.get("done"):
                    return await self._on_terminal(job_id, snap)
                prog = snap.get("progress") or {}
                new = ("running" if prog.get("phase") else "queued") if job["status"] != "cancelling" else "cancelling"
                step = (prog.get("phase"), prog.get("current_step"), prog.get("total_steps"))
                moved = step != (job["phase"], job["current_step"], job["total_steps"])
                ahead = running_owner = None
                if not prog.get("phase"):  # waiting: show its place in WanGP's shared queue
                    view = queue_view(await self.wangp.session("list_queue"), prefix, self.wangp.agent_context)
                    ahead, running_owner = view["ahead"], view["running"]
                last = now() if moved or not job["last_progress_at"] else job["last_progress_at"]
                stalled = int(bool(prog.get("phase")) and not moved and _age_s(last) > STALL_S)
                if stalled and not job["stalled"]:
                    log.warning("job %s: no progress from WanGP for %ss", job_id, STALL_S)
                    await self.db.audit("system", "render.stalled", job_id, {"phase": step[0], "step": step[1]})
                fields = (new, *step, ahead, running_owner, stalled)
                stored = (job["status"], job["phase"], job["current_step"], job["total_steps"],
                          job["queue_ahead"], job["queue_running"], job["stalled"])
                if fields != stored or moved or not job["last_progress_at"]:
                    # A cancel may land between our read and this write; never downgrade 'cancelling'.
                    await self.db.execute(
                        f"UPDATE {self.TABLE} SET status = CASE WHEN status = 'cancelling' THEN status ELSE ? END, "
                        "phase = ?, current_step = ?, total_steps = ?, queue_ahead = ?, queue_running = ?, "
                        "stalled = ?, last_progress_at = ?, "
                        "started_at = CASE WHEN ? = 'running' THEN COALESCE(started_at, ?) ELSE started_at END WHERE id = ?",
                        (*fields, last, new, now(), job_id))
                    if new == "running" and job["status"] != "running":
                        await self._set_shot_by_job(job_id, "rendering")
                    await self._emit(job_id, "render.stalled" if stalled and not job["stalled"] else "render.progress")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # network blips etc.: keep the job, retry following later
            log.exception("following %s failed", job_id)
            await self.db.execute(f"UPDATE {self.TABLE} SET error = ? WHERE id = ?", (f"tracking: {exc}", job_id))
            await asyncio.sleep(15)
            self._tasks.pop(job_id, None)
            if (await self.job(job_id))["status"] in ACTIVE:
                self._spawn(job_id)

    async def _on_terminal(self, job_id: str, snap: dict):
        job = await self.job(job_id)
        items = (snap.get("result") or {}).get("gallery_items") or snap.get("gallery_items") or []
        # done snapshots carry task ids only in gallery_items; interrupted ones list client_ids
        client_ids = (sorted({i["task_id"] for i in items if i.get("task_id")}) or snap.get("client_ids")
                      or json.loads(job["client_ids"]))
        await self.db.execute(f"UPDATE {self.TABLE} SET client_ids = ?, recovered = ? WHERE id = ?",
                              (json.dumps(client_ids), int(bool(snap.get("recovered"))), job_id))
        status = snap.get("status")
        if status == "done":
            finals = [i for i in items if i.get("role", "final") == "final"] or items
            if finals:
                return await self._ingest_outputs(job_id, finals)
            return await self._reconcile(job_id)
        if job["status"] == "cancelling":
            if job["phase"] in ("restored", "waiting for WanGP"):  # cancel of a restored task: see _reconcile
                return await self._reconcile(job_id)
            return await self._finish(job_id, "cancelled")
        if snap.get("recovered") or status in ("interrupted", "unknown_after_restart"):
            return await self._reconcile(job_id)
        errors = (snap.get("result") or {}).get("errors") or snap.get("error") or status
        return await self._finish(job_id, "failed", error=json.dumps(errors)[:1000])

    async def _reconcile(self, job_id: str):
        """WanGP restarted: follow a restored queue task, or find its output in the gallery."""
        job = await self.job(job_id)
        client_ids = set(json.loads(job["client_ids"]))
        prefix = f"mcp_{job['wangp_job_id']}_" if job["wangp_job_id"] else None

        def ours(task_id):
            return bool(task_id) and (task_id in client_ids or bool(prefix and task_id.startswith(prefix)))

        async def mark(phase):
            await self.db.execute(f"UPDATE {self.TABLE} SET status = CASE WHEN status = 'cancelling' THEN status "
                                  "ELSE 'restored' END, phase = ?, error = NULL WHERE id = ?", (phase, job_id))
            await self._set_shot_by_job(job_id, "rendering")
            await self._emit(job_id, "render.progress")
            return await self.job(job_id)

        if job["status"] not in ("restored", "cancelling"):
            job = await mark("waiting for WanGP")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RESTART_GRACE_S
        while True:
            try:
                job = await self.job(job_id)
                queue = await self.wangp.session("list_queue")
                queued = any(ours(t.get("client_id")) for t in queue.get("tasks", []))
                if job["status"] == "cancelling":
                    if queued and prefix:
                        queue_id = await self.wangp.cancel_own_queue_task(prefix)
                        await self.db.audit("owner", "render.cancel_restored", job_id, {"queue_id": queue_id})
                        await asyncio.sleep(POLL_WAIT_S)
                        continue
                    # Not queued: either it was and is now gone, or WanGP hasn't restored it yet
                    # (then keep watching, so a late restore is still cancelled, not left running).
                    if job["phase"] == "restored" or loop.time() > deadline:
                        return await self._finish(job_id, "cancelled")
                    await asyncio.sleep(POLL_WAIT_S)
                    continue
                if queued:
                    if job["phase"] != "restored":
                        job = await mark("restored")
                    deadline = loop.time() + RESTART_GRACE_S
                else:
                    gallery = await self.wangp.call("wangp_list_gallery",
                                                    {"media_type": self.gallery_type(job), "limit": GALLERY_SCAN})
                    matches = [m for m in gallery.get("media", [])
                               if ours(m.get("task_id")) and m.get("role", "final") == "final"]
                    if matches:
                        return await self._ingest_outputs(job_id, matches)
                    if loop.time() > deadline:
                        break
            except (WanGPError, httpx.HTTPError) as exc:  # WanGP still restarting
                log.info("reconciling %s: WanGP unavailable (%s)", job_id, type(exc).__name__)
                deadline = max(deadline, loop.time() + POLL_WAIT_S * 6)
            await asyncio.sleep(POLL_WAIT_S)
        return await self._finish(job_id, "interrupted",
                                  error="WanGP restarted and no output was found; render again to retry")

    async def _ingest(self, job_id: str, wangp_media_id: str):
        job = await self.job(job_id)
        take_id = new_id()
        rel_dir = Path(job["project_id"]) / job["shot_id"]
        (self.media_dir / rel_dir).mkdir(parents=True, exist_ok=True)
        video_rel, thumb_rel = rel_dir / f"{take_id}.mp4", rel_dir / f"{take_id}.jpg"
        video_path = self.media_dir / video_rel
        info = await self.wangp.download(wangp_media_id, video_path)
        digest = await asyncio.to_thread(sha256_file, video_path)
        if info.get("sha256") and info["sha256"] != digest:
            video_path.unlink(missing_ok=True)
            return await self._finish(job_id, "failed", error="downloaded file failed SHA-256 check")
        meta = await probe(video_path)
        await thumbnail(video_path, self.media_dir / thumb_rel, at_s=min(1.0, (meta["duration_s"] or 2) / 2))
        thumb_digest = await asyncio.to_thread(sha256_file, self.media_dir / thumb_rel)
        video_media, thumb_media = new_id(), new_id()
        provenance = await self._provenance(job, wangp_media_id, digest)

        def write(c):
            ts = now()
            c.execute("INSERT INTO media (id, project_id, kind, rel_path, sha256, bytes, width, height, fps, frames, "
                      "duration_s, has_audio, source, created_at) VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (video_media, job["project_id"], str(video_rel), digest, video_path.stat().st_size,
                       meta["width"], meta["height"], meta["fps"], meta["frames"], meta["duration_s"],
                       int(meta["has_audio"]), f"wangp:{wangp_media_id}", ts))
            c.execute("INSERT INTO media (id, project_id, kind, rel_path, sha256, bytes, source, created_at) "
                      "VALUES (?, ?, 'image', ?, ?, ?, 'thumbnail', ?)",
                      (thumb_media, job["project_id"], str(thumb_rel), thumb_digest,
                       (self.media_dir / thumb_rel).stat().st_size, ts))
            c.execute("INSERT INTO takes (id, shot_id, job_id, media_id, thumb_media_id, wangp_media_id, provenance, "
                      "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (take_id, job["shot_id"], job_id, video_media, thumb_media, wangp_media_id,
                       json.dumps(provenance), ts))
        await self.db.transaction(write)
        await self._finish(job_id, "done", take_id=take_id)

    async def _provenance(self, job: dict, wangp_media_id: str, digest: str) -> dict:
        """What exactly produced this take: WanGP's own record of the generation plus FilmLab's ids."""
        record = {"request_id": job["request_id"], "wangp_job_id": job["wangp_job_id"],
                  "wangp_media_id": wangp_media_id, "sha256": digest, "submitted_at": job["submitted_at"],
                  "ingested_at": now(), "mcp_server": self.wangp.server_info, "requested": json.loads(job["settings"]),
                  "references": json.loads(job.get("refs") or "[]")}
        try:
            res = await self.wangp.media_settings(wangp_media_id)
            used = res.get("settings") or {}
            record |= {"settings": used, "seed": used.get("seed"), "model_type": used.get("model_type"),
                       "model_file": used.get("model_filename"), "wangp_version": used.get("type"),
                       "steps": used.get("num_inference_steps"), "generation_s": used.get("generation_time"),
                       "filename": res.get("filename")}
        except (WanGPError, httpx.HTTPError) as exc:  # provenance is best effort; the take still counts
            log.warning("media_settings for %s failed: %s", wangp_media_id, exc)
            record["settings_error"] = str(exc)[:300]
        return record

    async def _finish(self, job_id: str, status: str, error: str | None = None, take_id: str | None = None):
        job = await self.job(job_id)
        target = await self._target_outcome(job, status)
        code = None if status == "done" else (
            "cancelled" if status == "cancelled" else errors.classify(error or job["error"] or status))

        def write(c):  # job and its target change together so no reader sees a done job on a rendering shot
            ts = now()
            c.execute(f"UPDATE {self.TABLE} SET status = ?, error = COALESCE(?, error), error_code = ?, finished_at = ?, "
                      "phase = NULL, queue_ahead = NULL, queue_running = NULL, stalled = 0 WHERE id = ?",
                      (status, error, code, ts, job_id))
            if target:
                target(c, ts)
        await self.db.transaction(write)
        await self._after_finish(job, status)
        await self._emit(job_id, f"{self.EVENT}.done" if status == "done" else f"{self.EVENT}.ended", take_id=take_id)

    async def _target_outcome(self, job: dict, status: str):
        """For a shot render: the shot's new status, written in the same transaction as the job."""
        has_take = await self.db.fetchone("SELECT 1 FROM takes WHERE shot_id = ? LIMIT 1", (job["shot_id"],))
        # A cancelled extra take leaves a shot that already has takes ready for review.
        shot_status = {"done": "ready", "cancelled": "ready" if has_take else "approved"}.get(status, "failed")
        job["_shot_status"] = shot_status
        return lambda c, ts: c.execute("UPDATE shots SET status = ?, updated_at = ? WHERE id = ?",
                                       (shot_status, ts, job["shot_id"]))

    async def _after_finish(self, job: dict, status: str):
        await self.bus.publish("shot.updated", {"shot_id": job["shot_id"], "status": job["_shot_status"]},
                               job["project_id"])
        for fn in ([self.on_finish] if self.on_finish else []) + self.listeners:
            try:  # the batch runner moves on, a redo run counts the render, the new take is inspected
                await fn(job, status)
            except Exception:  # noqa: BLE001 - a follow-up problem must never break a finished render
                log.exception("follow-up for job %s failed", job["id"])

    async def _ingest_outputs(self, job_id: str, finals: list[dict]):
        return await self._ingest(job_id, finals[0]["media_id"])

    # --- helpers ---------------------------------------------------------

    async def job(self, job_id: str) -> dict:
        row = await self.db.fetchone(f"SELECT * FROM {self.TABLE} WHERE id = ?", (job_id,))
        if not row:
            raise RenderError("unknown job")
        return row

    async def _set_shot(self, shot: dict, status: str):
        await self.db.execute("UPDATE shots SET status = ?, updated_at = ? WHERE id = ?", (status, now(), shot["id"]))
        await self.bus.publish("shot.updated", {"shot_id": shot["id"], "status": status}, shot["project_id"])

    async def _set_shot_by_job(self, job_id: str, status: str):
        job = await self.job(job_id)
        await self._set_shot({"id": job["shot_id"], "project_id": job["project_id"]}, status)

    async def _emit(self, job_id: str, type_: str, **extra):
        job = await self.job(job_id)
        payload = {"job_id": job_id, "shot_id": job["shot_id"], "status": job["status"], "phase": job["phase"],
                   "current_step": job["current_step"], "total_steps": job["total_steps"],
                   "recovered": bool(job["recovered"]), "error": job["error"],
                   "error_info": errors.describe(job["error_code"]), "queue_ahead": job["queue_ahead"],
                   "queue_running": job["queue_running"], "stalled": bool(job["stalled"]), **extra}
        if type_.endswith((".done", ".ended")):  # names for the cockpit's "render finished" notification
            names = await self.db.fetchone("SELECT s.title AS shot_title, s.position AS shot_position, p.title AS project_title "
                                           "FROM shots s JOIN projects p ON p.id = s.project_id WHERE s.id = ?", (job["shot_id"],))
            payload |= names or {}
        await self.bus.publish(type_, payload, job["project_id"])
