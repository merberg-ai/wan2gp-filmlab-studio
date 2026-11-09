"""Take review and the edit.

The edit is storyboard-driven: its clips are the project's shots in storyboard order, each with its
selected take (explicit, else the newest take not rejected). What the owner changes is kept in one JSON
document per project: per-clip trims, audio and transition length; up to two music tracks from the
library; title cards and captions; export settings. Every change is a revision that can be restored.
"""

import json

from . import shots
from .db import Database, new_id, now
from .events import EventBus

VERDICTS = ("good", "maybe", "reject")
MAX_MUSIC, MAX_TEXTS = 2, 40
CLIP_FIELDS = {"in", "out", "skip", "gain_db", "mute", "transition_s"}
POSITIONS = ("bottom", "center", "top")
DEFAULT_DOC = {"clips": {}, "music": [], "texts": [], "settings": {"loudness": True, "transition_s": 0.5,
                                                                    "mute_shots": False}}


class EditError(ValueError):
    pass


def _num(v, lo, hi, field, default=None):
    if v is None or v == "":
        if default is None:
            raise EditError(f"{field} is required")
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise EditError(f"{field} must be a number") from None
    if not lo <= x <= hi:
        raise EditError(f"{field} must be {lo}-{hi}")
    return x


def _text(v, limit, field, required=False):
    s = " ".join(str(v or "").split())
    if required and not s:
        raise EditError(f"{field} is required")
    if len(s) > limit:
        raise EditError(f"{field} must be at most {limit} characters")
    return s


class Edit:
    def __init__(self, db: Database, bus: EventBus, storyboard, library, media_dir):
        self.db, self.bus, self.storyboard, self.library = db, bus, storyboard, library
        self.media_dir = media_dir

    # --- take review -------------------------------------------------------

    async def review_take(self, take_id: str, verdict: str | None, notes: str | None, actor: str) -> dict:
        take = await self._take(take_id)
        fields = {}
        if verdict is not None:
            if verdict not in VERDICTS and verdict != "":
                raise EditError(f"verdict must be one of {', '.join(VERDICTS)} (or empty to clear)")
            fields["verdict"] = verdict or None
        if notes is not None:
            fields["notes"] = str(notes)[:2000]
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            await self.db.execute(f"UPDATE takes SET {sets} WHERE id = ?", (*fields.values(), take_id))
            if fields.get("verdict") == "reject":  # a rejected take can't stay the chosen one
                await self.db.execute("UPDATE shots SET selected_take_id = NULL WHERE selected_take_id = ?", (take_id,))
            await self.db.audit(actor, "take.review", take_id, fields)
            await self._changed(take["project_id"])
        return await self._take(take_id)

    async def select_take(self, shot_id: str, take_id: str | None, actor: str) -> dict:
        shot = await shots.get_shot(self.db, shot_id)
        if take_id:
            take = await self._take(take_id)
            if take["shot_id"] != shot_id:
                raise EditError("that take belongs to another shot")
            if take["verdict"] == "reject":
                raise EditError("that take is rejected; clear the verdict to use it")
        await self.db.execute("UPDATE shots SET selected_take_id = ? WHERE id = ?", (take_id, shot_id))
        await self.db.audit(actor, "take.select", shot_id, {"take_id": take_id})
        await self._changed(shot["project_id"])
        return await shots.get_shot(self.db, shot_id)

    async def _take(self, take_id: str) -> dict:
        t = await self.db.fetchone("SELECT t.*, s.project_id FROM takes t JOIN shots s ON s.id = t.shot_id "
                                   "WHERE t.id = ? AND s.deleted_at IS NULL", (take_id,))
        if not t:
            raise EditError("no such take")
        return t

    async def chosen_take(self, shot: dict) -> dict | None:
        """The take the edit uses: the explicit selection, else the newest take marked good, else the newest
        take not rejected; full-quality takes before previews."""
        rows = await self.db.fetchall(
            "SELECT t.id, t.verdict, t.created_at, t.preview, m.id AS media_id, m.rel_path, m.sha256, m.width, m.height, m.fps, "
            "m.frames, m.duration_s, m.has_audio FROM takes t JOIN media m ON m.id = t.media_id "
            "WHERE t.shot_id = ? AND t.deleted_at IS NULL ORDER BY t.rowid DESC", (shot["id"],))
        if shot.get("selected_take_id"):
            pick = next((r for r in rows if r["id"] == shot["selected_take_id"]), None)
            if pick:
                return pick
        for tier in ([r for r in rows if not r["preview"]], [r for r in rows if r["preview"]]):
            pick = next((r for r in tier if r["verdict"] == "good"), None) or \
                next((r for r in tier if r["verdict"] != "reject"), None)
            if pick:
                return pick
        return None

    # --- the edit document -------------------------------------------------

    async def doc(self, project_id: str) -> dict:
        row = await self.db.fetchone("SELECT doc FROM edits WHERE project_id = ?", (project_id,))
        doc = json.loads(row["doc"]) if row else json.loads(json.dumps(DEFAULT_DOC))
        for k, v in DEFAULT_DOC.items():
            doc.setdefault(k, json.loads(json.dumps(v)))
        return doc

    async def normalise(self, project_id: str, doc: dict) -> dict:
        """Validate a whole edit document; returns the cleaned copy."""
        if not isinstance(doc, dict):
            raise EditError("the edit must be an object")
        live = {s["id"]: s for s in await self.db.fetchall("SELECT * FROM shots WHERE project_id = ? AND deleted_at IS NULL",
                                                           (project_id,))}
        out = {"clips": {}, "music": [], "texts": [], "settings": {}}
        for sid, c in (doc.get("clips") or {}).items():
            if sid not in live or not isinstance(c, dict):
                continue  # clips of deleted shots simply drop out
            take = await self.chosen_take(live[sid])
            frames = (take or {}).get("frames") or live[sid]["frames"]
            # Trims are kept within the take (a newer, shorter take mustn't make the edit unsaveable).
            cin = min(int(_num(c.get("in"), 0, 100000, "in", 0)), max(frames - 1, 0))
            cout = min(int(_num(c.get("out"), 1, 100000, "out", frames)), frames)
            if cout <= cin:
                raise EditError(f"the out point of shot {live[sid]['position']} must be after its in point")
            out["clips"][sid] = {"in": cin, "out": cout, "skip": bool(c.get("skip")),
                                 "gain_db": _num(c.get("gain_db"), -40, 12, "gain_db", 0.0), "mute": bool(c.get("mute")),
                                 "transition_s": _num(c.get("transition_s"), 0, 3, "transition_s",
                                                      (doc.get("settings") or {}).get("transition_s", 0.5))}
        music = doc.get("music") or []
        if len(music) > MAX_MUSIC:
            raise EditError(f"at most {MAX_MUSIC} music tracks")
        for i, m in enumerate(music):
            f = await self.db.fetchone("SELECT * FROM library_files WHERE id = ? AND kind = 'audio'", (str(m.get("file_id")),))
            if not f:
                raise EditError(f"music[{i}]: choose an audio file from the library")
            out["music"].append({"id": m.get("id") or new_id()[:8], "file_id": f["id"],
                                 "start_s": _num(m.get("start_s"), 0, 3600, "start_s", 0.0),
                                 "offset_s": _num(m.get("offset_s"), 0, 3600, "offset_s", 0.0),
                                 "gain_db": _num(m.get("gain_db"), -40, 12, "gain_db", -6.0),
                                 "fade_in_s": _num(m.get("fade_in_s"), 0, 30, "fade_in_s", 0.0),
                                 "fade_out_s": _num(m.get("fade_out_s"), 0, 30, "fade_out_s", 1.0),
                                 "duck": bool(m.get("duck", True)),
                                 # Repeat the file to the end of the film (a short soundtrack under a longer film).
                                 "loop": bool(m.get("loop", False))})
        texts = doc.get("texts") or []
        if len(texts) > MAX_TEXTS:
            raise EditError(f"at most {MAX_TEXTS} titles and captions")
        for i, t in enumerate(texts):
            kind = t.get("kind")
            base = {"id": t.get("id") or new_id()[:8], "kind": kind,
                    "text": _text(t.get("text"), 200, f"texts[{i}].text", required=True),
                    "position": t.get("position") if t.get("position") in POSITIONS else
                    ("center" if kind == "title" else "bottom")}
            if kind == "title":
                at = t.get("at") or "start"
                if at not in ("start", "end") and at not in live:
                    raise EditError(f"texts[{i}].at must be start, end or a shot id")
                out["texts"].append(base | {"at": at, "duration_s": _num(t.get("duration_s"), 0.5, 15, "duration_s", 2.5)})
            elif kind == "caption":
                start = _num(t.get("start_s"), 0, 3600, f"texts[{i}].start_s")
                end = _num(t.get("end_s"), start + 0.2, 3600, f"texts[{i}].end_s")
                out["texts"].append(base | {"start_s": start, "end_s": end})
            else:
                raise EditError(f"texts[{i}].kind must be title or caption")
        s = doc.get("settings") or {}
        out["settings"] = {"loudness": bool(s.get("loudness", True)),
                           "transition_s": _num(s.get("transition_s"), 0, 3, "transition_s", 0.5),
                           # The soundtrack alone: every shot's own sound is muted (shots added later too).
                           "mute_shots": bool(s.get("mute_shots", False))}
        return out

    async def update(self, project_id: str, patch: dict, actor: str, note: str = "") -> dict:
        """Merge a change into the edit: clips merge per shot, music/texts replace, settings merge."""
        doc = await self.doc(project_id)
        for sid, c in (patch.get("clips") or {}).items():
            doc["clips"][sid] = {**doc["clips"].get(sid, {}), **(c or {})}
        for k in ("music", "texts"):
            if k in patch:
                doc[k] = patch[k]
        if "settings" in patch:
            doc["settings"] = {**doc["settings"], **(patch["settings"] or {})}
        clean = await self.normalise(project_id, doc)
        await self._save(project_id, clean, actor, note)
        return await self.timeline(project_id)

    async def _save(self, project_id: str, doc: dict, actor: str, note: str):
        ts, text = now(), json.dumps(doc, sort_keys=True)

        def write(c):
            c.execute("INSERT INTO edits (project_id, doc, version, updated_at) VALUES (?, ?, 1, ?) "
                      "ON CONFLICT(project_id) DO UPDATE SET doc = excluded.doc, version = version + 1, "
                      "updated_at = excluded.updated_at", (project_id, text, ts))
            c.execute("INSERT INTO edit_revisions (id, project_id, actor, note, doc, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (new_id(), project_id, actor, str(note)[:300], text, ts))
        await self.db.transaction(write)
        await self._changed(project_id)

    async def revisions(self, project_id: str) -> list[dict]:
        return await self.db.fetchall("SELECT id, actor, note, created_at FROM edit_revisions WHERE project_id = ? "
                                      "ORDER BY created_at DESC, rowid DESC LIMIT 100", (project_id,))

    async def restore(self, revision_id: str, actor: str) -> dict:
        rev = await self.db.fetchone("SELECT * FROM edit_revisions WHERE id = ?", (revision_id,))
        if not rev:
            raise EditError("no such edit revision")
        clean = await self.normalise(rev["project_id"], json.loads(rev["doc"]))
        await self._save(rev["project_id"], clean, actor, f"restored the edit from {rev['created_at'][:16].replace('T', ' ')}")
        return await self.timeline(rev["project_id"])

    # --- the timeline the cockpit and the exporter use ----------------------------

    async def timeline(self, project_id: str) -> dict:
        project = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        doc = await self.doc(project_id)
        fps = 24
        w, h = (int(x) for x in project["default_resolution"].split("x"))
        order = await self.storyboard.order(project_id)
        texts = doc["texts"]
        segments, issues, previews = [], [], []
        # A music video: the song plays under the whole film, so every shot's own sound is muted (a cut never
        # restarts the music).
        song_only = bool(project.get("music_mode") and project.get("song_file_id")) \
            or bool(doc["settings"].get("mute_shots"))  # or the owner chose "soundtrack only"

        def titles_at(key):
            for t in texts:
                if t["kind"] == "title" and t["at"] == key:
                    segments.append({"kind": "title", "id": t["id"], "text": t["text"], "text_position": t["position"],
                                     "frames": round(t["duration_s"] * fps), "transition": "cut", "transition_s": 0})

        titles_at("start")
        for sid in order:
            shot = await shots.get_shot(self.db, sid)
            titles_at(sid)
            c = doc["clips"].get(sid, {})
            if c.get("skip"):
                continue
            take = await self.chosen_take(shot)
            if not take:
                issues.append(f"shot {shot['position']} “{shot['title']}” has no usable take yet, so it isn't in the edit")
                continue
            frames = take["frames"] or round((take["duration_s"] or 0) * (take["fps"] or fps))
            cin = min(int(c.get("in", 0)), max(frames - 1, 0))
            cout = min(int(c.get("out") or frames), frames)
            if take["preview"]:
                previews.append(str(shot["position"]))
            if take["fps"] and round(take["fps"]) != fps:
                issues.append(f"shot {shot['position']} is {take['fps']:g} fps; it's conformed to {fps} fps on export")
            segments.append({
                "kind": "clip", "shot_id": sid, "position": shot["position"], "title": shot["title"], "take_id": take["id"],
                "media_id": take["media_id"], "rel_path": take["rel_path"], "sha256": take["sha256"],
                "src": {"width": take["width"], "height": take["height"], "fps": take["fps"], "frames": frames,
                        "has_audio": bool(take["has_audio"])},
                "in": cin, "out": cout, "frames": max(cout - cin, 1), "gain_db": c.get("gain_db", 0.0),
                "mute": bool(c.get("mute")) or song_only, "transition": shot["transition"] or "cut",
                "preview": bool(take["preview"]),
                "transition_s": c.get("transition_s", doc["settings"]["transition_s"])})
        titles_at("end")
        if previews:
            issues.append(f"shot{'s' if len(previews) > 1 else ''} {', '.join(previews)} use{'' if len(previews) > 1 else 's'} a "
                          "preview take (low resolution, scaled up on export); render full quality before the final export")
        if segments:
            segments[-1] = segments[-1] | {"transition": "cut"}  # nothing to transition into after the last one
        t = 0.0
        for i, seg in enumerate(segments):
            seg["start_s"] = round(t, 4)
            dur = seg["frames"] / fps
            nxt = segments[i + 1]["frames"] / fps if i + 1 < len(segments) else 0
            if seg["transition"] != "cut":
                seg["transition_s"] = round(min(float(seg["transition_s"]), dur / 2, nxt / 2), 3)
                if seg["transition_s"] <= 0:
                    seg["transition"] = "cut"
            if seg["transition"] == "cut":
                seg["transition_s"] = 0
            t += dur - seg["transition_s"]
        total = round(t, 3)
        for cap in (x for x in texts if x["kind"] == "caption"):
            if cap["start_s"] >= total:
                issues.append(f"caption “{cap['text'][:30]}” starts after the end of the film ({total:.1f} s)")
        issues += await self._music_overlap(doc["music"], total)
        return {"project_id": project_id, "fps": fps, "width": w, "height": h, "segments": segments,
                "total_s": total, "doc": doc, "issues": issues, "song_only": song_only}

    async def _music_overlap(self, music: list[dict], total: float) -> list[str]:
        """Two music tracks heard at the same time: usually a replaced soundtrack that was added a second time
        (two versions of one song drift apart and sound like an echo)."""
        spans = []
        for m in music:
            f = await self.db.fetchone("SELECT duration_s FROM library_files WHERE id = ?", (m["file_id"],))
            left = total - m["start_s"] if m.get("loop") else ((f or {}).get("duration_s") or 0) - m["offset_s"]
            spans.append((m["start_s"], min(total, m["start_s"] + max(0.0, left))))
        out = []
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                a, b = max(spans[i][0], spans[j][0]), min(spans[i][1], spans[j][1])
                if b - a >= 1.0:
                    out.append(f"music tracks {i + 1} and {j + 1} play at the same time ({a:.1f}-{b:.1f} s), so "
                               "they're heard on top of each other; if one replaces the other, remove the old one "
                               "in Soundtrack and music")
        return out

    async def _changed(self, project_id: str):
        await self.bus.publish("edit.updated", {}, project_id)

    # --- the director's suggestions -----------------------------------------------

    async def suggest(self, project_id: str, change: dict, note: str) -> dict:
        if not isinstance(change, dict) or not change or set(change) - {"clips", "texts_add", "settings"}:
            raise EditError("change must be an object with clips, texts_add and/or settings")
        clips = change.get("clips") or {}
        if not isinstance(clips, dict):
            raise EditError("clips must be an object keyed by shot id")
        live = {r["id"] for r in await self.db.fetchall(
            "SELECT id FROM shots WHERE project_id = ? AND deleted_at IS NULL", (project_id,))}
        for sid, c in clips.items():
            if sid not in live:
                raise EditError(f"shot {sid} isn't in this project (get_edit lists its clips)")
            extra = set(c or {}) - CLIP_FIELDS
            if extra:
                raise EditError(f"unknown clip field(s) {', '.join(sorted(extra))}; a clip takes "
                                f"{', '.join(sorted(CLIP_FIELDS))} (pick takes with the owner, not here)")
        trial = await self.doc(project_id)  # validate against the current edit; nothing is applied yet
        await self.normalise(project_id, self._apply(trial, change))
        sid = new_id()
        await self.db.execute("INSERT INTO edit_suggestions (id, project_id, change, note, created_at) VALUES (?, ?, ?, ?, ?)",
                              (sid, project_id, json.dumps(change), str(note)[:500], now()))
        await self._changed(project_id)
        return {"suggestion_id": sid, "status": "pending"}

    @staticmethod
    def _apply(doc: dict, change: dict) -> dict:
        doc = json.loads(json.dumps(doc))
        for sid, c in (change.get("clips") or {}).items():
            doc["clips"][sid] = {**doc["clips"].get(sid, {}), **(c or {})}
        doc["texts"] = doc["texts"] + list(change.get("texts_add") or [])
        if change.get("settings"):
            doc["settings"] = {**doc["settings"], **change["settings"]}
        return doc

    async def decide(self, suggestion_id: str, accept: bool, actor: str) -> dict:
        s = await self.db.fetchone("SELECT * FROM edit_suggestions WHERE id = ? AND status = 'pending'", (suggestion_id,))
        if not s:
            raise EditError("no pending suggestion")
        if accept:
            doc = self._apply(await self.doc(s["project_id"]), json.loads(s["change"]))
            await self._save(s["project_id"], await self.normalise(s["project_id"], doc), actor,
                             f"accepted the director's suggestion: {s['note'][:200]}")
        await self.db.execute("UPDATE edit_suggestions SET status = ?, decided_at = ? WHERE id = ?",
                              ("accepted" if accept else "rejected", now(), suggestion_id))
        await self.db.audit(actor, "edit.suggestion", suggestion_id, {"accepted": accept})
        await self._changed(s["project_id"])
        return {"suggestion_id": suggestion_id, "accepted": accept}

    async def suggestions(self, project_id: str) -> list[dict]:
        rows = await self.db.fetchall("SELECT * FROM edit_suggestions WHERE project_id = ? AND status = 'pending' "
                                      "ORDER BY created_at", (project_id,))
        for r in rows:
            r["change"] = json.loads(r["change"])
        return rows
