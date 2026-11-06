"""Reference library: assets, variants (looks), their files, project cast and shot references.

The library is shared by all projects. Files are stored once per content hash under
media_dir/library. A locked variant can't change; "new version" copies it to version + 1,
so shots that point at the old version keep exactly what they were rendered with.
"""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from . import shots
from .db import Database, new_id, now
from .events import EventBus
from .media import _run, sha256_file

KINDS = ("character", "location", "prop", "style", "sound")
IMAGE_TYPES = {"png": "image/png", "mjpeg": "image/jpeg", "webp": "image/webp"}
AUDIO_TYPES = {"pcm_s16le": "audio/wav", "pcm_s24le": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_SIDE, MAX_PIXELS = 8192, 40_000_000  # a small file can still decode to a huge image
MAX_REFS_PER_SHOT = 8


class LibraryError(ValueError):
    pass


def _text(value, limit, field, required=False):
    value = str(value or "").strip()
    if required and not value:
        raise LibraryError(f"{field} is required")
    if len(value) > limit:
        raise LibraryError(f"{field} must be at most {limit} characters")
    return value


async def inspect_file(path: Path) -> dict:
    """Decode the file with ffprobe: only real PNG/JPEG/WebP images or WAV/MP3/FLAC audio pass."""
    try:
        out = await _run("ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path))
    except RuntimeError:
        raise LibraryError("not a readable image or audio file") from None
    info = json.loads(out)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video and video.get("codec_name") in IMAGE_TYPES and not audio:
        frames = video.get("nb_frames")
        if frames and frames.isdigit() and int(frames) > 1:
            raise LibraryError("animated images aren't supported; use a still")
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        if not w or not h or max(w, h) > MAX_SIDE or w * h > MAX_PIXELS:
            raise LibraryError(f"images must be at most {MAX_SIDE} pixels on a side (this one is {w}x{h})")
        return {"kind": "image", "mime": IMAGE_TYPES[video["codec_name"]],
                "width": video.get("width"), "height": video.get("height"), "duration_s": None}
    if audio and not video and audio.get("codec_name") in AUDIO_TYPES:
        return {"kind": "audio", "mime": AUDIO_TYPES[audio["codec_name"]], "width": None, "height": None,
                "duration_s": float(info.get("format", {}).get("duration") or 0) or None}
    raise LibraryError("only PNG, JPEG or WebP images and WAV, MP3 or FLAC audio are accepted")


class Library:
    def __init__(self, db: Database, bus: EventBus, media_dir: Path):
        self.db, self.bus = db, bus
        self.root = media_dir / "library"

    # --- files -----------------------------------------------------------

    async def store_file(self, src: Path, source: str) -> dict:
        """Add a file to the library (or return the existing one with the same content)."""
        meta = await inspect_file(src)
        digest = await asyncio.to_thread(sha256_file, src)
        existing = await self.db.fetchone("SELECT * FROM library_files WHERE sha256 = ?", (digest,))
        if existing:
            return existing | {"deduplicated": True}
        file_id = new_id()
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "audio/wav": "wav",
               "audio/mpeg": "mp3", "audio/flac": "flac"}[meta["mime"]]
        sub = Path(digest[:2])
        (self.root / sub).mkdir(parents=True, exist_ok=True)
        rel, thumb_rel = sub / f"{digest}.{ext}", None
        await asyncio.to_thread(shutil.copyfile, src, self.root / rel)  # original bytes, unchanged
        if meta["kind"] == "image":
            thumb_rel = sub / f"{digest}.thumb.jpg"
            await _run("ffmpeg", "-y", "-v", "error", "-i", str(self.root / rel), "-frames:v", "1",
                       "-vf", "scale=320:320:force_original_aspect_ratio=decrease", str(self.root / thumb_rel))
        await self.db.execute(
            "INSERT INTO library_files (id, kind, rel_path, thumb_rel_path, sha256, bytes, mime, width, height, "
            "duration_s, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, meta["kind"], str(rel), str(thumb_rel) if thumb_rel else None, digest, src.stat().st_size,
             meta["mime"], meta["width"], meta["height"], meta["duration_s"], source, now()))
        return await self.file(file_id) | {"deduplicated": False}

    async def store_bytes(self, data: bytes, source: str) -> dict:
        if len(data) > MAX_UPLOAD_BYTES:
            raise LibraryError(f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "upload"
            path.write_bytes(data)
            return await self.store_file(path, source)

    async def file(self, file_id: str) -> dict:
        row = await self.db.fetchone("SELECT * FROM library_files WHERE id = ?", (file_id,))
        if not row:
            raise LibraryError("no such library file")
        return row

    def path(self, row: dict, thumb: bool = False) -> Path:
        rel = row["thumb_rel_path"] if thumb else row["rel_path"]
        path = (self.root / rel).resolve()
        if self.root.resolve() not in path.parents:
            raise LibraryError("file outside the library")
        return path

    # --- assets and variants ---------------------------------------------

    async def create_asset(self, fields: dict, actor: str, draft: bool = False) -> dict:
        """draft: made by the Creator and hidden from the library until the owner approves it."""
        kind = fields.get("kind")
        if kind not in KINDS:
            raise LibraryError(f"kind must be one of {', '.join(KINDS)}")
        asset_id, variant_id, ts = new_id(), new_id(), now()

        def write(c):
            c.execute("INSERT INTO assets (id, kind, name, description, notes, tags, created_by, created_at, updated_at, "
                      "draft) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (asset_id, kind, _text(fields.get("name"), 120, "name", True),
                       _text(fields.get("description"), 2000, "description"), _text(fields.get("notes"), 4000, "notes"),
                       _text(fields.get("tags"), 500, "tags"), actor, ts, ts, int(draft)))
            c.execute("INSERT INTO variants (id, asset_id, name, created_at, updated_at) VALUES (?, ?, 'default', ?, ?)",
                      (variant_id, asset_id, ts, ts))
        await self.db.transaction(write)
        await self._reindex(asset_id)
        await self.db.audit(actor, "asset.create", asset_id, {"kind": kind})
        await self.bus.publish("library.updated", {"asset_id": asset_id})
        return await self.asset(asset_id)

    async def update_asset(self, asset_id: str, fields: dict, actor: str) -> dict:
        await self.asset(asset_id)
        data = {k: _text(fields[k], lim, k, k == "name") for k, lim in
                (("name", 120), ("description", 2000), ("notes", 4000), ("tags", 500)) if k in fields}
        if data:
            sets = ", ".join(f"{k} = ?" for k in data)
            await self.db.execute(f"UPDATE assets SET {sets}, updated_at = ? WHERE id = ?", (*data.values(), now(), asset_id))
            await self._reindex(asset_id)
            await self.db.audit(actor, "asset.update", asset_id, sorted(data))
            await self.bus.publish("library.updated", {"asset_id": asset_id})
        return await self.asset(asset_id)

    async def asset(self, asset_id: str) -> dict:
        asset = await self.db.fetchone("SELECT * FROM assets WHERE id = ? AND deleted_at IS NULL", (asset_id,))
        if not asset:
            raise LibraryError("no such asset")
        variants = await self.db.fetchall("SELECT * FROM variants WHERE asset_id = ? AND deleted_at IS NULL "
                                          "ORDER BY name, version", (asset_id,))
        files = await self.db.fetchall(
            "SELECT vf.id AS entry_id, vf.variant_id, vf.position, vf.caption, vf.source_note, vf.license_note, "
            "vf.provenance, f.id AS file_id, f.kind, f.mime, f.width, f.height, f.duration_s, f.sha256, f.source, "
            "f.bytes FROM variant_files vf JOIN library_files f ON f.id = vf.file_id "
            "JOIN variants v ON v.id = vf.variant_id WHERE v.asset_id = ? AND v.deleted_at IS NULL ORDER BY vf.position",
            (asset_id,))
        by_variant: dict[str, list] = {}
        for f in files:
            f["provenance"] = json.loads(f["provenance"]) if f["provenance"] else None
            by_variant.setdefault(f["variant_id"], []).append(f)
        for v in variants:
            v["locked"] = bool(v["locked"])
            v["files"] = by_variant.get(v["id"], [])
        voice = None
        if asset.get("voice_file_id"):
            voice = await self.db.fetchone("SELECT id AS file_id, mime, duration_s, bytes FROM library_files WHERE id = ?",
                                           (asset["voice_file_id"],))
        return asset | {"variants": variants, "voice": voice}

    # --- voices ----------------------------------------------------------

    async def set_voice(self, asset_id: str, data: bytes, actor: str) -> dict:
        """A character's voice sample (WAV, MP3 or FLAC, 2-30 s of clean speech): its voice reference in renders."""
        from .sound import VOICE_MAX_S, VOICE_MIN_S
        asset = await self.asset(asset_id)
        if asset["kind"] != "character":
            raise LibraryError("only characters have a voice")
        return await self.set_voice_file(asset_id, (await self.store_bytes(data, "upload"))["id"], actor)

    async def set_voice_file(self, asset_id: str, file_id: str, actor: str) -> dict:
        """A library audio file (uploaded, or a generated voice the owner kept) as the character's voice."""
        from .sound import VOICE_MAX_S, VOICE_MIN_S
        asset = await self.asset(asset_id)
        if asset["kind"] != "character":
            raise LibraryError("only characters have a voice")
        f = await self.file(file_id)
        if f["kind"] != "audio":
            raise LibraryError("a voice sample must be a WAV, MP3 or FLAC recording")
        if not f["duration_s"] or not VOICE_MIN_S <= f["duration_s"] <= VOICE_MAX_S:
            raise LibraryError(f"a voice sample must be {VOICE_MIN_S:g}-{VOICE_MAX_S:g} s long "
                               f"(this one is {f['duration_s'] or 0:.1f} s); 5-15 s of clear speech works best")
        await self.db.execute("UPDATE assets SET voice_file_id = ?, updated_at = ? WHERE id = ?", (f["id"], now(), asset_id))
        await self.db.audit(actor, "asset.voice", asset_id, {"file_id": f["id"]})
        await self.bus.publish("library.updated", {"asset_id": asset_id})
        return await self.asset(asset_id)

    async def clear_voice(self, asset_id: str, actor: str) -> dict:
        await self.asset(asset_id)
        await self.db.execute("UPDATE assets SET voice_file_id = NULL, updated_at = ? WHERE id = ?", (now(), asset_id))
        await self.db.audit(actor, "asset.voice", asset_id, {"file_id": None})
        await self.bus.publish("library.updated", {"asset_id": asset_id})
        return await self.asset(asset_id)

    async def search(self, query: str = "", kind: str | None = None, limit: int = 200,
                     include_drafts: bool = False) -> list[dict]:
        sql = ("SELECT a.*, (SELECT COUNT(*) FROM variants v WHERE v.asset_id = a.id AND v.deleted_at IS NULL) "
               "AS variant_count, (SELECT f.id FROM variant_files vf JOIN variants v ON v.id = vf.variant_id "
               "JOIN library_files f ON f.id = vf.file_id WHERE v.asset_id = a.id AND v.deleted_at IS NULL "
               "AND f.kind = 'image' ORDER BY v.version DESC, vf.position LIMIT 1) AS cover_file_id "
               "FROM assets a WHERE a.deleted_at IS NULL" + ("" if include_drafts else " AND a.draft = 0"))
        params: list = []
        if kind:
            sql += " AND a.kind = ?"
            params.append(kind)
        terms = [t for t in str(query).replace('"', " ").split() if t]
        if terms:
            sql += " AND a.id IN (SELECT asset_id FROM library_search WHERE library_search MATCH ?)"
            params.append(" ".join(f'"{t}"*' for t in terms))
        sql += " ORDER BY a.kind, a.name LIMIT ?"
        params.append(limit)
        return await self.db.fetchall(sql, tuple(params))

    async def _reindex(self, asset_id: str):
        a = await self.db.fetchone("SELECT kind, name, description, notes, tags FROM assets WHERE id = ?", (asset_id,))
        rows = await self.db.fetchall(
            "SELECT v.name, v.description, vf.caption FROM variants v LEFT JOIN variant_files vf ON vf.variant_id = v.id "
            "WHERE v.asset_id = ?", (asset_id,))
        text = " ".join([a["kind"], a["name"], a["description"], a["notes"], a["tags"]]
                        + [" ".join(filter(None, (r["name"], r["description"], r["caption"]))) for r in rows])

        def write(c):
            c.execute("DELETE FROM library_search WHERE asset_id = ?", (asset_id,))
            c.execute("INSERT INTO library_search (asset_id, text) VALUES (?, ?)", (asset_id, text))
        await self.db.transaction(write)

    async def variant(self, variant_id: str) -> dict:
        v = await self.db.fetchone("SELECT v.* FROM variants v JOIN assets a ON a.id = v.asset_id WHERE v.id = ? "
                                   "AND v.deleted_at IS NULL AND a.deleted_at IS NULL", (variant_id,))
        if not v:
            raise LibraryError("no such variant")
        return v

    async def mutable_variant(self, variant_id: str) -> dict:
        v = await self.variant(variant_id)
        if v["locked"]:
            raise LibraryError(f"'{v['name']}' v{v['version']} is locked; make a new version to change it")
        return v

    async def create_variant(self, asset_id: str, name: str, description: str, actor: str) -> dict:
        await self.asset(asset_id)
        vid, ts = new_id(), now()
        await self.db.execute("INSERT INTO variants (id, asset_id, name, description, created_at, updated_at) "
                              "VALUES (?, ?, ?, ?, ?, ?)",
                              (vid, asset_id, _text(name, 80, "name", True), _text(description, 1000, "description"), ts, ts))
        await self._reindex(asset_id)
        await self.db.audit(actor, "variant.create", vid, {"asset_id": asset_id})
        await self.bus.publish("library.updated", {"asset_id": asset_id})
        return await self.variant(vid)

    async def update_variant(self, variant_id: str, fields: dict, actor: str) -> dict:
        v = await self.mutable_variant(variant_id)
        data = {k: _text(fields[k], lim, k, k == "name") for k, lim in (("name", 80), ("description", 1000)) if k in fields}
        if data:
            sets = ", ".join(f"{k} = ?" for k in data)
            await self.db.execute(f"UPDATE variants SET {sets}, updated_at = ? WHERE id = ?", (*data.values(), now(), variant_id))
            await self._reindex(v["asset_id"])
            await self.db.audit(actor, "variant.update", variant_id, sorted(data))
            await self.bus.publish("library.updated", {"asset_id": v["asset_id"]})
        return await self.variant(variant_id)

    async def set_locked(self, variant_id: str, locked: bool, actor: str) -> dict:
        v = await self.variant(variant_id)
        await self.db.execute("UPDATE variants SET locked = ?, updated_at = ? WHERE id = ?", (int(locked), now(), variant_id))
        await self.db.audit(actor, "variant.lock" if locked else "variant.unlock", variant_id)
        await self.bus.publish("library.updated", {"asset_id": v["asset_id"]})
        return await self.variant(variant_id)

    async def new_version(self, variant_id: str, actor: str) -> dict:
        """Copy a variant (normally a locked one) to version + 1, unlocked, with the same files."""
        v = await self.variant(variant_id)
        latest = await self.db.fetchone("SELECT MAX(version) AS v FROM variants WHERE asset_id = ? AND name = ?",
                                        (v["asset_id"], v["name"]))
        new_vid, ts = new_id(), now()

        def write(c):
            c.execute("INSERT INTO variants (id, asset_id, name, description, version, parent_id, created_at, updated_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (new_vid, v["asset_id"], v["name"], v["description"], latest["v"] + 1, variant_id, ts, ts))
            c.execute("INSERT INTO variant_files (id, variant_id, file_id, position, caption, source_note, license_note, "
                      "provenance, created_at) SELECT lower(hex(randomblob(16))), ?, file_id, position, caption, "
                      "source_note, license_note, provenance, ? FROM variant_files WHERE variant_id = ?",
                      (new_vid, ts, variant_id))
        await self.db.transaction(write)
        await self.db.audit(actor, "variant.new_version", new_vid, {"from": variant_id, "version": latest["v"] + 1})
        await self.bus.publish("library.updated", {"asset_id": v["asset_id"]})
        return await self.variant(new_vid)

    async def add_file(self, variant_id: str, file_id: str, meta: dict, actor: str, provenance: dict | None = None) -> dict:
        v = await self.mutable_variant(variant_id)
        await self.file(file_id)
        dup = await self.db.fetchone("SELECT id FROM variant_files WHERE variant_id = ? AND file_id = ?", (variant_id, file_id))
        if dup:
            return {"entry_id": dup["id"], "already_attached": True}
        pos = await self.db.fetchone("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM variant_files WHERE variant_id = ?",
                                     (variant_id,))
        entry = new_id()
        await self.db.execute(
            "INSERT INTO variant_files (id, variant_id, file_id, position, caption, source_note, license_note, provenance, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry, variant_id, file_id, pos["p"], _text(meta.get("caption"), 500, "caption"),
             _text(meta.get("source_note"), 500, "source_note"), _text(meta.get("license_note"), 500, "license_note"),
             json.dumps(provenance) if provenance else None, now()))
        await self._reindex(v["asset_id"])
        await self.db.audit(actor, "variant.add_file", variant_id, {"file_id": file_id})
        await self.bus.publish("library.updated", {"asset_id": v["asset_id"]})
        return {"entry_id": entry, "already_attached": False}

    async def update_file_entry(self, entry_id: str, meta: dict, actor: str) -> dict:
        entry = await self.db.fetchone("SELECT * FROM variant_files WHERE id = ?", (entry_id,))
        if not entry:
            raise LibraryError("no such file entry")
        v = await self.mutable_variant(entry["variant_id"])
        data = {k: _text(meta[k], 500, k) for k in ("caption", "source_note", "license_note") if k in meta}
        if data:
            sets = ", ".join(f"{k} = ?" for k in data)
            await self.db.execute(f"UPDATE variant_files SET {sets} WHERE id = ?", (*data.values(), entry_id))
            await self._reindex(v["asset_id"])
            await self.db.audit(actor, "variant.update_file", entry_id, sorted(data))
            await self.bus.publish("library.updated", {"asset_id": v["asset_id"]})
        return {"entry_id": entry_id}

    async def remove_file_entry(self, entry_id: str, actor: str):
        entry = await self.db.fetchone("SELECT * FROM variant_files WHERE id = ?", (entry_id,))
        if not entry:
            raise LibraryError("no such file entry")
        v = await self.mutable_variant(entry["variant_id"])
        await self.db.execute("DELETE FROM variant_files WHERE id = ?", (entry_id,))
        await self.db.audit(actor, "variant.remove_file", entry_id)
        await self.bus.publish("library.updated", {"asset_id": v["asset_id"]})

    # --- project cast and shot references ----------------------------------

    async def cast(self, project_id: str) -> list[dict]:
        return await self.db.fetchall(
            "SELECT a.* FROM project_cast pc JOIN assets a ON a.id = pc.asset_id WHERE pc.project_id = ? "
            "AND a.deleted_at IS NULL "
            "ORDER BY a.kind, a.name", (project_id,))

    async def add_to_cast(self, project_id: str, asset_id: str, actor: str):
        await self.asset(asset_id)
        await self.db.execute("INSERT OR IGNORE INTO project_cast (project_id, asset_id, added_at) VALUES (?, ?, ?)",
                              (project_id, asset_id, now()))
        await self.db.audit(actor, "cast.add", project_id, {"asset_id": asset_id})
        await self.bus.publish("project.updated", {"project_id": project_id}, project_id)

    async def shot_refs(self, shot_id: str) -> list[dict]:
        return await self.db.fetchall(
            "SELECT r.position, r.role, r.variant_id, r.file_id, v.name AS variant_name, v.version, v.locked, "
            "a.id AS asset_id, a.name AS asset_name, a.kind, a.description AS asset_description, f.sha256, f.width, f.height FROM shot_refs r "
            "JOIN variants v ON v.id = r.variant_id JOIN assets a ON a.id = v.asset_id "
            "JOIN library_files f ON f.id = r.file_id WHERE r.shot_id = ? ORDER BY r.position", (shot_id,))

    async def set_shot_refs(self, shot: dict, refs: list[dict], actor: str, rationale: str = "") -> list[dict]:
        """Replace a shot's ordered references. Each ref names a variant and optionally which of its images
        (default: the variant's first image); the shot keeps pointing at that exact variant version."""
        before = await shots.snapshot(self.db, shot["id"])
        if len(refs) > MAX_REFS_PER_SHOT:
            raise LibraryError(f"at most {MAX_REFS_PER_SHOT} references per shot")
        resolved = []
        for i, ref in enumerate(refs, start=1):
            v = await self.variant(str(ref.get("variant_id") or ""))
            images = await self.db.fetchall(
                "SELECT vf.file_id FROM variant_files vf JOIN library_files f ON f.id = vf.file_id "
                "WHERE vf.variant_id = ? AND f.kind = 'image' ORDER BY vf.position", (v["id"],))
            ids = [r["file_id"] for r in images]
            file_id = ref.get("file_id") or (ids[0] if ids else None)
            if not file_id:
                raise LibraryError(f"'{v['name']}' v{v['version']} has no images to use as a reference")
            if file_id not in ids:
                raise LibraryError("that image isn't part of the chosen variant")
            resolved.append((shot["id"], i, v["id"], file_id, _text(ref.get("role"), 40, "role")))
            await self.add_to_cast(shot["project_id"], v["asset_id"], actor)

        def write(c):
            c.execute("DELETE FROM shot_refs WHERE shot_id = ?", (shot["id"],))
            c.executemany("INSERT INTO shot_refs (shot_id, position, variant_id, file_id, role) VALUES (?, ?, ?, ?, ?)",
                          resolved)
        await self.db.transaction(write)
        await self.db.audit(actor, "shot.refs", shot["id"], [r[2] for r in resolved])
        await shots.record(self.db, shot["id"], actor, "refs", before, rationale)
        return await self.shot_refs(shot["id"])
