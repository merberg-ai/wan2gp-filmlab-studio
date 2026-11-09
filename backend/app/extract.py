"""Extract: a new library asset from a frame of a take (or any picture), optionally reworked by a WanGP image model
that takes reference pictures (e.g. "the same person, full body, plain background") before it becomes the asset's look.

1. grab()  a frame of a take at a time, or a crop of a picture, stored as a library image (not yet in any asset).
2. create() the asset: the picture as its look, or the picture kept in a "Source frame" look and a WanGP job on the
   main look that makes candidates from it; the owner keeps one on the asset's page, as with any generated image.
"""

import tempfile
from pathlib import Path

from .db import Database
from .library import Library, LibraryError
from .media import _run

IMAGE_KINDS = ("character", "location", "prop", "style")
SOURCE_LOOK = "source frame"  # lower case: looks are listed by name, so the main look ("default") stays first


class ExtractError(ValueError):
    pass


def _crop_filter(crop: dict | None) -> str | None:
    """crop: {x, y, w, h} as fractions of the picture (0-1), from the cockpit's selection."""
    if not crop:
        return None
    try:
        x, y, w, h = (float(crop[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        raise ExtractError("crop needs x, y, w and h as fractions of the picture") from None
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)
    if w < 0.02 or h < 0.02:
        raise ExtractError("the selection is too small")
    # even sizes, so the result encodes anywhere
    return (f"crop=trunc(iw*{w:.5f}/2)*2:trunc(ih*{h:.5f}/2)*2:trunc(iw*{x:.5f}):trunc(ih*{y:.5f})")


class Extractor:
    def __init__(self, db: Database, library: Library, media_dir: Path):
        self.db, self.library, self.media_dir = db, library, media_dir

    async def grab(self, media_id: str | None = None, file_id: str | None = None, t: float = 0.0,
                   crop: dict | None = None) -> dict:
        """A frame of a take's video (media_id, at t seconds) or a library picture (file_id), optionally cropped."""
        if bool(media_id) == bool(file_id):
            raise ExtractError("give a take's media_id or a picture's file_id")
        note = ""
        if media_id:
            m = await self.db.fetchone("SELECT kind, rel_path, duration_s FROM media WHERE id = ?", (media_id,))
            if not m:
                raise ExtractError("no such take")
            src = (self.media_dir / m["rel_path"]).resolve()
            if self.media_dir.resolve() not in src.parents or not src.is_file():
                raise ExtractError("the take's file is missing")
            t = max(0.0, min(float(t or 0), max(0.0, (m["duration_s"] or 0) - 0.04)))
            seek = ["-ss", f"{t:.3f}"] if m["kind"] == "video" else []
            take = await self.db.fetchone(
                "SELECT t.id, s.title, s.position, p.title AS project FROM takes t JOIN shots s ON s.id = t.shot_id "
                "JOIN projects p ON p.id = s.project_id WHERE t.media_id = ?", (media_id,))
            note = (f"Frame at {t:.2f} s of shot {take['position']} “{take['title']}” in {take['project']}" if take
                    else f"Frame at {t:.2f} s")
        else:
            f = await self.library.file(file_id)
            if f["kind"] != "image":
                raise ExtractError("only pictures can be cropped")
            src, seek = self.library.path(f), []
        vf = _crop_filter(crop)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "frame.png"
            await _run("ffmpeg", "-y", "-v", "error", *seek, "-i", str(src), "-frames:v", "1",
                       *(["-vf", vf] if vf else []), str(out))
            if not out.is_file():
                raise ExtractError("no frame there (past the end of the take?)")
            stored = await self.library.store_file(out, "upload")
        return {k: stored[k] for k in ("id", "width", "height", "sha256")} | {
            "file_id": stored["id"], "source_note": note + (" (cropped)" if crop else "")}

    async def create(self, s, fields: dict, file_id: str, source_note: str = "", edit: dict | None = None) -> dict:
        """The new asset, from the picture; with edit {prompt, template, count, resolution} a WanGP job on its main
        look makes candidates from the picture (it stays in the "Source frame" look)."""
        if fields.get("kind") not in IMAGE_KINDS:
            raise ExtractError(f"a picture becomes a {', '.join(IMAGE_KINDS[:-1])} or {IMAGE_KINDS[-1]}")
        f = await self.library.file(file_id)
        if f["kind"] != "image":
            raise ExtractError("only pictures can become an asset's look")
        if edit:
            if s.renders.gate and (held := await s.renders.gate()):  # before anything is made
                raise ExtractError(held)
            if len(str(edit.get("prompt") or "").strip()) < 3:
                raise ExtractError("say what the model should make from the picture")
        try:
            asset = await self.library.create_asset(fields, "owner")
        except LibraryError as exc:
            raise ExtractError(str(exc)) from exc
        main = asset["variants"][0]["id"]
        meta = {"caption": "extracted", "source_note": source_note[:500]}
        out = {"asset_id": asset["id"], "job_id": None, "error": None}
        if not edit:
            await self.library.add_file(main, file_id, meta, "owner")
            return out
        source = await self.library.create_variant(asset["id"], SOURCE_LOOK,
                                                   "The picture this asset was extracted from", "owner")
        await self.library.add_file(source["id"], file_id, meta, "owner")
        try:
            job = await s.images.submit(main, edit["prompt"], "owner", edit.get("template") or None, [file_id],
                                        edit.get("count"), edit.get("resolution") or None, label="extract")
            out["job_id"] = job["id"]
        except Exception as exc:  # noqa: BLE001 - the asset stays; the owner can generate again on its page
            out["error"] = f"The asset was made, but the model couldn't start: {exc}"
        return out
