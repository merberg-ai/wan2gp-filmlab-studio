"""Getting library images into WanGP and preparing a shot's references for a render.

Each library file is uploaded to FilmLab's WanGP workspace gallery once; the returned gallery id is
cached per workspace (wangp_uploads). If WanGP has lost an item ("Unknown WanGP media_id"), the cache
entry is dropped and the file uploaded again.
"""

import logging
from pathlib import Path

from . import sound
from .capabilities import Capabilities
from .db import Database, now
from .library import Library
from .renders import RenderService
from .wangp.client import WanGPClient

log = logging.getLogger("filmlab.references")
UNKNOWN_MEDIA = "Unknown WanGP media_id"


class References:
    def __init__(self, db: Database, wangp: WanGPClient, library: Library, renders: RenderService):
        self.db, self.wangp, self.library, self.renders = db, wangp, library, renders
        self.caps: Capabilities | None = None  # set by Studio (they need each other)

    def _workspace(self) -> str:
        return (self.renders.workspace or {}).get("workspace_id") or "default"

    async def wangp_id(self, file_row: dict) -> str:
        ws = self._workspace()
        cached = await self.db.fetchone("SELECT wangp_media_id FROM wangp_uploads WHERE file_id = ? AND workspace_id = ?",
                                        (file_row["id"], ws))
        if cached:
            return cached["wangp_media_id"]
        ext = Path(file_row["rel_path"]).suffix
        item = await self.wangp.upload_gallery(self.library.path(file_row), file_row["mime"],
                                               f"filmlab-{file_row['sha256'][:12]}{ext}")
        await self.db.execute("INSERT OR REPLACE INTO wangp_uploads (file_id, workspace_id, wangp_media_id, uploaded_at) "
                              "VALUES (?, ?, ?, ?)", (file_row["id"], ws, item["media_id"], now()))
        log.info("uploaded library file %s to WanGP as %s", file_row["id"], item["media_id"])
        return item["media_id"]

    async def wangp_id_for_path(self, path: Path, sha256: str, mime: str) -> str:
        """For FilmLab's own files outside the library (the capability probe image)."""
        key = f"wangp.upload.{self._workspace()}.{sha256}"
        cached = await self.db.kv_get(key)
        if cached:
            return cached
        item = await self.wangp.upload_gallery(path, mime, f"filmlab-probe-{sha256[:12]}{path.suffix}")
        await self.db.kv_set(key, item["media_id"])
        return item["media_id"]

    async def forget(self, file_ids: list[str]):
        ws = self._workspace()
        for fid in file_ids:
            await self.db.execute("DELETE FROM wangp_uploads WHERE file_id = ? AND workspace_id = ?", (fid, ws))

    async def upload_refs(self, refs: list[dict]) -> list[dict]:
        out = []
        for r in refs:
            f = await self.library.file(r["file_id"])
            out.append({"position": r["position"], "role": r["role"], "asset_id": r["asset_id"],
                        "asset_name": r["asset_name"], "kind": r["kind"], "variant_id": r["variant_id"],
                        "variant_name": r["variant_name"], "version": r["version"], "file_id": r["file_id"],
                        "sha256": r["sha256"], "wangp_media_id": await self.wangp_id(f)})
        return out

    async def prepare_shot(self, shot: dict) -> tuple[dict, list[dict], list[dict]]:
        """Effective template, uploaded references (and the speakers' voices, as shot["_voices"]) and pre-render
        issues for a shot."""
        refs = await self.library.shot_refs(shot["id"])
        shot = await self.sync_template(shot, len(refs))
        template = shot["template"]
        sound_fields = sound.load(shot.get("sound"))
        voiced = await self.voiced(sound_fields, refs) if sound.is_h3(shot["model_type"]) else []
        issues = await self.caps.checks(template, len(refs), shot["prompt"], await self.caps.setting_for(shot),
                                        sound_fields=sound_fields, refs=refs, voiced=voiced)
        if any(i["level"] == "block" for i in issues):
            return shot, [], issues  # don't upload anything for a render that can't happen
        return shot | {"_voices": await self.upload_voices(voiced)}, await self.upload_refs(refs), issues

    async def voiced(self, sound_fields: dict | None, refs: list[dict]) -> list[dict]:
        """The speaking characters that have a voice sample, in speaking order (H3 takes up to three)."""
        out = []
        for asset_id in sound.voices_needed(sound_fields, refs):
            row = await self.db.fetchone("SELECT a.name, f.* FROM assets a JOIN library_files f ON f.id = a.voice_file_id "
                                         "WHERE a.id = ? AND a.deleted_at IS NULL", (asset_id,))
            if row:
                out.append({"asset_id": asset_id, "asset_name": row.pop("name"), "file": row})
        return out

    async def upload_voices(self, voiced: list[dict]) -> list[dict]:
        return [{"asset_id": v["asset_id"], "asset_name": v["asset_name"], "file_id": v["file"]["id"], "n": n,
                 "wangp_media_id": await self.wangp_id(v["file"])}
                for n, v in enumerate(voiced[:sound.MAX_VOICES], start=1)]

    async def reupload_voices(self, shot: dict) -> dict:
        voices = shot.get("_voices") or []
        if not voices:
            return shot
        await self.forget([v["file_id"] for v in voices])
        return shot | {"_voices": [v | {"wangp_media_id": await self.wangp_id(await self.library.file(v["file_id"]))}
                                   for v in voices]}

    async def sync_template(self, shot: dict, ref_count: int | None = None) -> dict:
        """A shot following the defaults: keep its stored template matching whether it has references."""
        if ref_count is None:
            ref_count = len(await self.library.shot_refs(shot["id"]))
        project = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (shot["project_id"],))
        template = await self.caps.effective_template(shot, project, ref_count)
        if template == shot["template"]:
            return shot
        tpl = await self.renders.resolve_template(template)
        await self.db.execute("UPDATE shots SET template = ?, model_type = ? WHERE id = ?",
                              (tpl["template"], tpl["model_type"], shot["id"]))
        return shot | {"template": tpl["template"], "model_type": tpl["model_type"]}

    async def sync_template_soon(self, shot: dict, ref_count: int):
        """Best effort, for reference edits: if WanGP can't be asked now, the render does it."""
        try:
            await self.sync_template(shot, ref_count)
        except Exception:  # noqa: BLE001
            log.warning("couldn't update the template of shot %s now; the render will", shot["id"], exc_info=True)

    async def reupload(self, refs: list[dict]) -> list[dict]:
        await self.forget([r["file_id"] for r in refs])
        return [r | {"wangp_media_id": await self.wangp_id(await self.library.file(r["file_id"]))} for r in refs]
