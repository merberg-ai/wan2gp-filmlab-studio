"""The Storybook: a project as an illustrated book, as a picture book (one picture a page, narration below) or a
comic (2-6 panels a page, caption boxes and speech bubbles), exported by the server as a PDF, PNG pages (zipped) and
a CBZ comic archive (storybook_render.py draws the pages).

The book is one document per project (with a history, like the edit). Each shot of the storyboard is a panel: its
picture is a frame of a take (pinned: a new take of the shot is offered, never swapped in silently), optionally
cropped, with narration (the owner's or the director's, see StoryWriter) and the shot's own dialogue."""

import asyncio
import hashlib
import json
import logging
import shutil
import zipfile
from pathlib import Path

from . import shots
from .db import new_id, now
from .extract import ExtractError, _crop_filter
from .media import _run

log = logging.getLogger("filmlab.storybook")

LAYOUTS = ("picture", "comic")
PANEL_FIELDS = {"take_id", "t", "crop", "narration", "dialogue", "skip", "break_before"}
DEFAULT_DOC = {"layout": "picture", "per_page": 4, "chapters": True,
               "cover": {"title": "", "subtitle": "", "author": "", "shot_id": None, "crop": None}, "panels": {}}
MAX_NARRATION = 600


class StorybookError(ValueError):
    pass


def _lines(sound) -> list[dict]:
    if isinstance(sound, str):
        try:
            sound = json.loads(sound)
        except ValueError:
            return []
    out = []
    for ln in (sound or {}).get("lines") or []:
        if isinstance(ln, dict) and str(ln.get("text") or "").strip():
            out.append({"speaker": str(ln.get("speaker") or "").strip(), "text": " ".join(str(ln["text"]).split()),
                        "delivery": str(ln.get("delivery") or "").strip()})
    return out


def _crop(c) -> dict | None:
    if not c:
        return None
    try:
        _crop_filter(c)
    except ExtractError as exc:
        raise StorybookError(str(exc)) from exc
    return {k: round(float(c[k]), 4) for k in ("x", "y", "w", "h")}


class Storybook:
    def __init__(self, studio):
        self.s = studio
        self.db, self.bus = studio.db, studio.bus
        self.media_dir: Path = studio.settings.media_dir
        self._tasks: dict[str, asyncio.Task] = {}
        self._frame_locks: dict[str, asyncio.Lock] = {}

    # --- the document ---------------------------------------------------------------

    async def doc(self, project_id: str) -> dict:
        row = await self.db.fetchone("SELECT doc FROM storybooks WHERE project_id = ?", (project_id,))
        d = json.loads(row["doc"]) if row else {}
        out = json.loads(json.dumps(DEFAULT_DOC)) | {k: v for k, v in d.items() if k in DEFAULT_DOC}
        out["cover"] = DEFAULT_DOC["cover"] | (d.get("cover") or {})
        return out

    def _clean(self, doc: dict, live: set[str]) -> dict:
        if doc.get("layout") not in LAYOUTS:
            raise StorybookError(f"layout must be one of {', '.join(LAYOUTS)}")
        try:
            per = int(doc.get("per_page") or 4)
        except (TypeError, ValueError):
            raise StorybookError("per_page must be a number from 2 to 6") from None
        if not 2 <= per <= 6:
            raise StorybookError("per_page must be from 2 to 6")
        c = doc.get("cover") or {}
        cover = {"title": " ".join(str(c.get("title") or "").split())[:120],
                 "subtitle": " ".join(str(c.get("subtitle") or "").split())[:200],
                 "author": " ".join(str(c.get("author") or "").split())[:120],
                 "shot_id": c.get("shot_id") if c.get("shot_id") in live else None, "crop": _crop(c.get("crop"))}
        panels = {}
        for sid, p in (doc.get("panels") or {}).items():
            if sid not in live or not isinstance(p, dict):
                continue
            extra = set(p) - PANEL_FIELDS
            if extra:
                raise StorybookError(f"unknown panel field(s): {', '.join(sorted(extra))}")
            t = p.get("t")
            panels[sid] = {"take_id": p.get("take_id") or None,
                           "t": None if t in (None, "") else max(0.0, round(float(t), 3)),
                           "crop": _crop(p.get("crop")),
                           "narration": str(p.get("narration") or "").strip()[:MAX_NARRATION],
                           "dialogue": p.get("dialogue", True) is not False, "skip": bool(p.get("skip")),
                           "break_before": bool(p.get("break_before"))}
        return {"layout": doc["layout"], "per_page": per, "chapters": bool(doc.get("chapters", True)),
                "cover": cover, "panels": panels}

    async def _live(self, project_id: str) -> set[str]:
        return {r["id"] for r in await self.db.fetchall(
            "SELECT id FROM shots WHERE project_id = ? AND deleted_at IS NULL", (project_id,))}

    async def update(self, project_id: str, patch: dict, actor: str, note: str = "") -> dict:
        """Merge a change: panels merge per shot, the cover merges, the rest replaces."""
        doc = await self.doc(project_id)
        for k in ("layout", "per_page", "chapters"):
            if k in patch and patch[k] is not None:
                doc[k] = patch[k]
        if patch.get("cover"):
            doc["cover"] = doc["cover"] | patch["cover"]
        for sid, p in (patch.get("panels") or {}).items():
            doc["panels"][sid] = {**doc["panels"].get(sid, {}), **(p or {})}
        await self._save(project_id, self._clean(doc, await self._live(project_id)), actor, note)
        return await self.view(project_id)

    async def _save(self, project_id: str, doc: dict, actor: str, note: str, history: bool = True):
        ts, text = now(), json.dumps(doc, sort_keys=True)

        def write(c):
            c.execute("INSERT INTO storybooks (project_id, doc, version, updated_at) VALUES (?, ?, 1, ?) "
                      "ON CONFLICT(project_id) DO UPDATE SET doc = excluded.doc, version = version + 1, "
                      "updated_at = excluded.updated_at", (project_id, text, ts))
            if history:
                c.execute("INSERT INTO storybook_revisions (id, project_id, actor, note, doc, created_at) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (new_id(), project_id, actor, str(note)[:300], text, ts))
        await self.db.transaction(write)
        await self.bus.publish("storybook.updated", {}, project_id)

    async def revisions(self, project_id: str) -> list[dict]:
        return await self.db.fetchall("SELECT id, actor, note, created_at FROM storybook_revisions WHERE project_id = ? "
                                      "ORDER BY created_at DESC, rowid DESC LIMIT 30", (project_id,))

    async def restore(self, project_id: str, revision_id: str, actor: str) -> dict:
        rev = await self.db.fetchone("SELECT * FROM storybook_revisions WHERE id = ? AND project_id = ?",
                                     (revision_id, project_id))
        if not rev:
            raise StorybookError("no such storybook revision")
        clean = self._clean(json.loads(rev["doc"]), await self._live(project_id))
        await self._save(project_id, clean, actor, f"restored the book from {rev['created_at'][:16].replace('T', ' ')}")
        return await self.view(project_id)

    # --- the book as it reads ------------------------------------------------------------

    async def view(self, project_id: str) -> dict:
        """Panels in storyboard order with their take and words, and the pages they make. A shot without a panel yet
        gets one pinned to the take the edit uses (saved without a history entry)."""
        project = await self.db.fetchone("SELECT title, logline, default_resolution FROM projects WHERE id = ?",
                                         (project_id,))
        doc = await self.doc(project_id)
        scenes = {sc["id"]: sc for sc in await self.s.storyboard.scenes(project_id)}
        panels, pinned = [], False
        for sid in await self.s.storyboard.order(project_id):
            shot = await shots.get_shot(self.db, sid)
            chosen = await self.s.edit.chosen_take(shot)
            p = doc["panels"].get(sid)
            if p is None:
                p = doc["panels"][sid] = {"take_id": chosen["id"] if chosen else None, "t": None, "crop": None,
                                          "narration": "", "dialogue": True, "skip": False, "break_before": False}
                pinned = True
            elif not p.get("take_id") and chosen:
                p["take_id"], pinned = chosen["id"], True
            take = await self._take(p["take_id"]) if p.get("take_id") else None
            if p.get("take_id") and not take:  # the pinned take was deleted: follow the edit again
                p["take_id"], p["t"], pinned = (chosen["id"] if chosen else None), None, True
                take = await self._take(p["take_id"]) if p["take_id"] else None
            scene = scenes.get(shot["scene_id"])
            dur = (take or {}).get("duration_s") or 0
            t = p["t"] if p.get("t") is not None else round(dur / 2, 3)
            panels.append({
                "shot_id": sid, "position": shot["position"], "title": shot["title"],
                "idea": shot.get("idea") or shot["prompt"] or "",
                "scene_id": shot["scene_id"], "scene": scene["title"] if scene else None,
                "scene_mood": (scene or {}).get("mood") or None,
                "take_id": take["id"] if take else None, "media_id": take["media_id"] if take else None,
                "duration_s": dur, "fps": (take or {}).get("fps") or 24,
                "width": (take or {}).get("width"), "height": (take or {}).get("height"),
                "t": min(t, max(0.0, dur - 0.05)) if dur else 0, "crop": p.get("crop"),
                "narration": p.get("narration") or "", "lines": _lines(shot.get("sound")),
                "dialogue": p.get("dialogue", True), "skip": bool(p.get("skip")),
                "break_before": bool(p.get("break_before")),
                "new_take_id": chosen["id"] if chosen and take and chosen["id"] != take["id"] else None,
            })
        if pinned:
            await self._save(project_id, self._clean(doc, await self._live(project_id)), "system", "", history=False)
        if not doc["cover"]["title"]:
            doc["cover"]["title"] = project["title"]
        w, h = (int(x) for x in project["default_resolution"].split("x"))
        return {"project_id": project_id, "doc": doc, "panels": panels, "pages": self.pages(doc, panels),
                "portrait": h > w, "logline": project.get("logline") or "",
                "revisions": await self.revisions(project_id)}

    async def _take(self, take_id: str) -> dict | None:
        return await self.db.fetchone(
            "SELECT t.id, m.id AS media_id, m.rel_path, m.duration_s, m.fps, m.width, m.height, s.project_id "
            "FROM takes t JOIN media m ON m.id = t.media_id JOIN shots s ON s.id = t.shot_id "
            "WHERE t.id = ? AND t.deleted_at IS NULL", (take_id,))

    @staticmethod
    def pages(doc: dict, panels: list[dict]) -> list[dict]:
        """Pages after the cover: {chapter, shot_ids}. A picture book has one panel a page; a comic fills pages of
        per_page panels. With chapters, a new scene starts a new page under the scene's title."""
        out: list[dict] = []
        scene = object()
        for p in panels:
            if p["skip"] or not p["take_id"]:
                continue
            new_scene = doc["chapters"] and p["scene_id"] != scene
            chapter = p["scene"] if new_scene else None
            scene = p["scene_id"]
            full = not out or len(out[-1]["shot_ids"]) >= (1 if doc["layout"] == "picture" else doc["per_page"])
            if full or new_scene or p["break_before"]:
                out.append({"chapter": chapter, "shot_ids": []})
            out[-1]["shot_ids"].append(p["shot_id"])
        return out

    # --- frames -------------------------------------------------------------------------------

    async def frame(self, take_id: str, t: float, crop: dict | None, size: str = "small") -> Path:
        """A JPEG of the take at t (cropped), cached under the project's media folder."""
        take = await self._take(take_id)
        if not take:
            raise StorybookError("no such take")
        crop = _crop(crop)
        src = (self.media_dir / take["rel_path"]).resolve()
        if self.media_dir.resolve() not in src.parents or not src.is_file():
            raise StorybookError("the take's file is missing")
        dur = take["duration_s"] or 0
        t = max(0.0, min(float(t or 0), max(0.0, dur - 0.05)))
        key = hashlib.sha1(json.dumps([take_id, round(t, 3), crop, size]).encode()).hexdigest()[:20]
        out = self.media_dir / take["project_id"] / "storybook" / "frames" / f"{key}.jpg"
        if out.is_file():
            return out
        lock = self._frame_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not out.is_file():
                out.parent.mkdir(parents=True, exist_ok=True)
                vf = [x for x in (_crop_filter(crop), "scale='min(640,iw)':-2" if size == "small"
                                  else "scale='min(1800,iw)':-2") if x]
                tmp = out.with_suffix(".tmp.jpg")
                await _run("ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                           "-vf", ",".join(vf), "-q:v", "3", str(tmp))
                if not tmp.is_file():
                    raise StorybookError("no frame there")
                tmp.replace(out)
        self._frame_locks.pop(key, None)
        return out

    # --- exports ----------------------------------------------------------------------------------

    async def exports(self, project_id: str) -> list[dict]:
        return await self.db.fetchall("SELECT * FROM storybook_exports WHERE project_id = ? ORDER BY created_at DESC "
                                      "LIMIT 20", (project_id,))

    async def export_row(self, export_id: str) -> dict:
        row = await self.db.fetchone("SELECT * FROM storybook_exports WHERE id = ?", (export_id,))
        if not row:
            raise StorybookError("no such storybook export")
        return row

    async def start_export(self, project_id: str, actor: str) -> dict:
        if any(e["status"] in ("queued", "running") for e in await self.exports(project_id)):
            raise StorybookError("the book is already being exported")
        view = await self.view(project_id)
        if not view["pages"]:
            raise StorybookError("the book has no pages yet: it needs shots with takes that aren't left out")
        eid = new_id()
        await self.db.execute("INSERT INTO storybook_exports (id, project_id, status, layout, created_at) "
                              "VALUES (?, ?, 'queued', ?, ?)", (eid, project_id, view["doc"]["layout"], now()))
        await self.db.audit(actor, "storybook.export", project_id, {"export_id": eid})
        self._tasks[eid] = asyncio.create_task(self._run_export(eid, view))
        self._tasks[eid].add_done_callback(lambda _: self._tasks.pop(eid, None))
        await self.bus.publish("storybook.export", {"export_id": eid, "status": "queued"}, project_id)
        return await self.export_row(eid)

    async def _run_export(self, eid: str, view: dict):
        from .storybook_render import render_book  # Pillow is only loaded when a book is drawn
        pid = view["project_id"]
        rel = f"{pid}/storybook/exports/{eid}"
        folder = self.media_dir / rel
        try:
            await self.db.execute("UPDATE storybook_exports SET status = 'running', rel_dir = ? WHERE id = ?", (rel, eid))
            await self.bus.publish("storybook.export", {"export_id": eid, "status": "running"}, pid)
            by_id = {p["shot_id"]: p for p in view["panels"]}
            pictures: dict[str, Path] = {}
            for page in view["pages"]:
                for sid in page["shot_ids"]:
                    p = by_id[sid]
                    pictures[sid] = await self.frame(p["take_id"], p["t"], p["crop"], "full")
            cover_sid = view["doc"]["cover"]["shot_id"] or view["pages"][0]["shot_ids"][0]
            cp = by_id.get(cover_sid)
            cover = (await self.frame(cp["take_id"], cp["t"], view["doc"]["cover"]["crop"] or cp["crop"], "full")
                     if cp and cp["take_id"] else pictures[view["pages"][0]["shot_ids"][0]])
            folder.mkdir(parents=True, exist_ok=True)
            n = await asyncio.to_thread(render_book, view, pictures, cover, folder)
            size = sum(f.stat().st_size for f in folder.iterdir() if f.is_file())
            await self.db.execute("UPDATE storybook_exports SET status = 'done', pages = ?, bytes = ?, finished_at = ? "
                                  "WHERE id = ?", (n, size, now(), eid))
        except asyncio.CancelledError:
            await self.db.execute("UPDATE storybook_exports SET status = 'failed', error = 'stopped', finished_at = ? "
                                  "WHERE id = ?", (now(), eid))
            raise
        except Exception as exc:  # noqa: BLE001 - the owner sees why
            log.exception("storybook export %s failed", eid)
            await self.db.execute("UPDATE storybook_exports SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                                  (f"{type(exc).__name__}: {exc}"[:400], now(), eid))
        await self.bus.publish("storybook.export", {"export_id": eid}, pid)

    def export_file(self, row: dict, what: str) -> tuple[Path, str, str]:
        """(path, media type, download name) of book.pdf, pages.zip, book.cbz or page-N.png."""
        if row["status"] != "done":
            raise StorybookError("the export isn't finished")
        names = {"pdf": ("book.pdf", "application/pdf"), "zip": ("pages.zip", "application/zip"),
                 "cbz": ("book.cbz", "application/vnd.comicbook+zip")}
        if what in names:
            name, mime = names[what]
        elif what.startswith("page-") and what[5:].isdigit():
            name, mime = f"page-{int(what[5:]):03d}.png", "image/png"
        else:
            raise StorybookError("pdf, zip, cbz or page-N")
        path = (self.media_dir / row["rel_dir"] / name).resolve()
        if self.media_dir.resolve() not in path.parents or not path.is_file():
            raise StorybookError("the file is missing")
        return path, mime, name

    async def delete_export(self, export_id: str, actor: str) -> dict:
        row = await self.export_row(export_id)
        if row["status"] in ("queued", "running"):
            if task := self._tasks.get(export_id):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if row["rel_dir"]:
            await asyncio.to_thread(shutil.rmtree, self.media_dir / row["rel_dir"], True)
        await self.db.execute("DELETE FROM storybook_exports WHERE id = ?", (export_id,))
        await self.db.audit(actor, "storybook.export_delete", export_id)
        await self.bus.publish("storybook.export", {"export_id": export_id}, row["project_id"])
        return {"deleted": export_id}

    async def resume_all(self):
        """An export the backend was drawing when it stopped is marked failed (the owner exports again)."""
        await self.db.execute("UPDATE storybook_exports SET status = 'failed', error = 'interrupted by a restart', "
                              "finished_at = ? WHERE status IN ('queued', 'running')", (now(),))

    async def shutdown(self):
        for t in list(self._tasks.values()):
            t.cancel()


def zip_pages(folder: Path, pages: list[Path], jpeg_pages: list[Path]):
    with zipfile.ZipFile(folder / "pages.zip", "w", zipfile.ZIP_STORED) as z:
        for p in pages:
            z.write(p, p.name)
    with zipfile.ZipFile(folder / "book.cbz", "w", zipfile.ZIP_STORED) as z:
        for p in jpeg_pages:
            z.write(p, p.name)
