"""Reference image generation with WanGP's image models (and, for the Creator, voices and music).

An image job asks WanGP for N candidates of a variant's image (text -> image, or from library images
as references). It reuses the render lifecycle from RenderService: request_id idempotency, queue
position, restart reconciliation, stalls and error translation. Finished candidates are stored in the
library but only become part of the variant when the owner keeps them.
"""

import json
import tempfile
from pathlib import Path

from .capabilities import AUDIO_TASKS, IMAGE_TASKS, Capabilities
from .db import new_id, now
from .library import Library, LibraryError
from .references import References
from .renders import ACTIVE, RenderError, RenderService
from .wangp.client import WanGPError

MAX_CANDIDATES = 4


class ImageJobs(RenderService):
    TABLE = "image_jobs"
    EVENT = "image"
    GALLERY_TYPE = "image"

    def __init__(self, renders: RenderService, library: Library, refs: References, caps: Capabilities):
        super().__init__(renders.db, renders.bus, renders.wangp, renders.media_dir, renders.workspace_name)
        self.renders, self.library, self.refs, self.caps = renders, library, refs, caps

    def gallery_type(self, job: dict) -> str:
        return job.get("media") or "image"

    async def submit(self, variant_id: str, prompt: str, actor: str, template: str | None = None,
                     input_file_ids: list[str] | None = None, count: int | None = None,
                     resolution: str | None = None, seed: int | None = None, label: str | None = None,
                     media: str = "image", extra: dict | None = None) -> dict:
        """media "audio": a voice (gen_speech_from_description: prompt = the words, extra.alt_prompt = the voice) or
        music (gen_song: prompt = lyrics or [Instrumental], extra.alt_prompt = the style); template is then required."""
        if self.renders.gate and (held := await self.renders.gate()):
            raise RenderError(held)
        variant = await self.library.variant(variant_id)
        prompt = str(prompt or "").strip()
        if not 3 <= len(prompt) <= 4000:
            raise RenderError("prompt must be 3-4000 characters")
        settings_cfg = await self.caps.settings()
        input_file_ids = list(dict.fromkeys(input_file_ids or []))
        count = int(count or settings_cfg["image_candidates"])
        if not 1 <= count <= MAX_CANDIDATES:
            raise RenderError(f"candidates must be 1-{MAX_CANDIDATES}")
        if media == "audio" and (input_file_ids or not template):
            raise RenderError("audio generation takes a voice or music template and no images")
        template = template or settings_cfg["image_refs" if input_file_ids else "image_text"]
        matrix = await self.caps.matrix()
        try:
            entry = self.caps.ensure_usable(matrix, template, AUDIO_TASKS if media == "audio" else IMAGE_TASKS)
        except ValueError as exc:
            raise RenderError(str(exc)) from exc
        if input_file_ids and entry["refs"] == "none":
            raise RenderError(f"{template} takes no reference images; pick one that does, e.g. {settings_cfg['image_refs']}")
        inputs = []
        for fid in input_file_ids:
            f = await self.library.file(fid)
            if f["kind"] != "image":
                raise RenderError("only images can be references")
            inputs.append({"file_id": fid, "sha256": f["sha256"], "wangp_media_id": await self.refs.wangp_id(f)})
        full = await self.wangp.call("wangp_templates", {"task": entry["task"], "template": template})
        base = dict(full.get("settings") or {}, prompt=prompt) | {k: v for k, v in (extra or {}).items()
                                                                   if k in ("alt_prompt", "duration_seconds")}
        if resolution and media == "image":
            base["resolution"] = resolution
        if inputs:
            base["image_refs"] = [i["wangp_media_id"] for i in inputs]
        # One submission with a settings list: WanGP queues the candidates together, in order.
        batch = [base | {"seed": seed + i if seed is not None and seed >= 0 else -1} for i in range(count)]
        job_id = new_id()
        await self.db.execute(
            "INSERT INTO image_jobs (id, asset_id, variant_id, request_id, settings, template, prompt, count, "
            "input_file_ids, status, submitted_at, label, media) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitting', ?, ?, ?)",
            (job_id, variant["asset_id"], variant_id, f"filmlab-img-{job_id}", json.dumps(batch), template, prompt,
             count, json.dumps(inputs), now(), (str(label).strip()[:80] or None) if label else None, media))
        await self.db.audit(actor, "image.submit", variant_id, {"job_id": job_id, "template": template, "count": count})
        try:
            await self._submit_to_wangp(job_id)
        except WanGPError as exc:
            await self._finish(job_id, "failed", error=str(exc))
            raise RenderError(f"WanGP rejected the image generation: {exc}") from exc
        self._spawn(job_id)
        return await self.job(job_id)

    async def active_for_variant(self, variant_id: str) -> list[dict]:
        return await self.db.fetchall(f"SELECT * FROM image_jobs WHERE variant_id = ? AND status IN {ACTIVE}", (variant_id,))

    # --- lifecycle hooks -------------------------------------------------------

    async def _ingest_outputs(self, job_id: str, finals: list[dict]):
        job = await self.job(job_id)
        inputs = json.loads(job["input_file_ids"])
        stored = 0
        for item in finals:
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / "candidate"
                info = await self.wangp.download(item["media_id"], dest)
                try:
                    f = await self.library.store_file(dest, "generated")
                except LibraryError as exc:
                    await self.db.audit("system", "image.bad_output", job_id, {"media_id": item["media_id"], "error": str(exc)})
                    continue
            if info.get("sha256") and info["sha256"] != f["sha256"]:
                await self.db.audit("system", "image.sha_mismatch", job_id, {"media_id": item["media_id"]})
                continue
            record = await self._provenance(job, item["media_id"], f["sha256"])
            record |= {"template": job["template"], "prompt": job["prompt"], "inputs": inputs}
            await self.db.execute(
                "INSERT INTO image_candidates (id, job_id, file_id, wangp_media_id, provenance, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (new_id(), job_id, f["id"], item["media_id"], json.dumps(record), now()))
            stored += 1
        if not stored:
            return await self._finish(job_id, "failed", error="WanGP finished but no usable image came back")
        return await self._finish(job_id, "done")

    async def _target_outcome(self, job: dict, status: str):
        return None  # the variant only changes when the owner keeps a candidate

    async def _after_finish(self, job: dict, status: str):
        await self.bus.publish("library.updated", {"asset_id": job["asset_id"], "image_job": job["id"]})

    async def _set_shot_by_job(self, job_id: str, status: str):
        return None

    async def _emit(self, job_id: str, type_: str, **extra):
        job = await self.job(job_id)
        await self.bus.publish(type_, {"job_id": job_id, "asset_id": job["asset_id"], "variant_id": job["variant_id"],
                                       "status": job["status"], "phase": job["phase"],
                                       "current_step": job["current_step"], "total_steps": job["total_steps"],
                                       "queue_ahead": job["queue_ahead"], "queue_running": job["queue_running"],
                                       "stalled": bool(job["stalled"]), "error": job["error"]})

    # --- candidates ---------------------------------------------------------------

    async def candidate(self, candidate_id: str) -> dict:
        row = await self.db.fetchone(
            "SELECT c.*, j.variant_id, j.asset_id, j.template, j.label, j.media FROM image_candidates c "
            "JOIN image_jobs j ON j.id = c.job_id "
            "WHERE c.id = ?", (candidate_id,))
        if not row:
            raise LibraryError("no such candidate")
        return row

    async def keep(self, candidate_id: str, meta: dict, actor: str) -> dict:
        c = await self.candidate(candidate_id)
        if c["status"] != "candidate":
            raise LibraryError(f"candidate is already {c['status']}")
        prov = json.loads(c["provenance"] or "{}")
        model = prov.get("model_type") or c["template"]
        if c["media"] == "audio" and c["label"] == "voice":  # a designed voice: the character's voice sample
            await self.library.set_voice_file(c["asset_id"], c["file_id"], actor)
            await self.db.execute("UPDATE image_candidates SET status = 'kept' WHERE id = ?", (candidate_id,))
            return {"voice": True}
        meta = {"source_note": f"Generated with WanGP · {c['template']}",
                "license_note": f"Generated locally with {model}"} | ({"caption": c["label"]} if c["label"] else {})             | {k: v for k, v in meta.items() if v}
        res = await self.library.add_file(c["variant_id"], c["file_id"], meta, actor, provenance=prov)
        await self.db.execute("UPDATE image_candidates SET status = 'kept' WHERE id = ?", (candidate_id,))
        return res

    async def discard(self, candidate_id: str, actor: str):
        c = await self.candidate(candidate_id)
        if c["status"] == "candidate":
            await self.db.execute("UPDATE image_candidates SET status = 'discarded' WHERE id = ?", (candidate_id,))
            await self.db.audit(actor, "image.discard", candidate_id)
            await self.bus.publish("library.updated", {"asset_id": c["asset_id"]})

    async def for_asset(self, asset_id: str) -> dict:
        jobs = await self.db.fetchall(
            "SELECT id, variant_id, template, prompt, count, status, phase, current_step, total_steps, queue_ahead, "
            "queue_running, stalled, error, error_code, submitted_at, finished_at, label, media FROM image_jobs "
            "WHERE asset_id = ? "
            "ORDER BY submitted_at DESC LIMIT 20", (asset_id,))
        cands = await self.db.fetchall(
            "SELECT c.id, c.job_id, c.file_id, c.status, c.provenance, c.created_at, j.variant_id, j.label, j.media "
            "FROM image_candidates c "
            "JOIN image_jobs j ON j.id = c.job_id WHERE j.asset_id = ? AND c.status = 'candidate' ORDER BY c.created_at",
            (asset_id,))
        for c in cands:
            p = json.loads(c.pop("provenance") or "{}")
            c["seed"], c["template"] = p.get("seed"), p.get("template")
        return {"jobs": jobs, "candidates": cands}
