"""Music videos: one song under the whole film.

A music-video project has a song (an audio file of the Library: generated here from exact lyrics and a style, or
uploaded) and a method:

- lipsync: each shot is rendered by a song-driven model (WanGP's gen_video_with_speech) from its own slice of the
  song, so the performer sings exactly that part. A shot's slice starts where the shot starts in the film (the
  shots before it, back to back) and lasts as long as the shot.
- soundtrack: shots are rendered as usual, as picture; the song plays over them.

Either way the edit plays the song from start to end under the whole film with the shots' own sound muted, so a
cut never restarts the music, and the preview, the animatic and the export all hear the same song.
"""

import asyncio
import hashlib
import logging
from pathlib import Path

from .capabilities import MUSIC_TASKS, SPEECH_VIDEO_TASKS
from .db import now
from .media import _run

log = logging.getLogger("filmlab.music_video")

MODES = ("lipsync", "soundtrack")
# The song-driven models FilmLab suggests, best first. H3 keeps the performers' look from their pictures (the
# library references); Infinitalk is a dedicated lip-sync model that uses one picture; LTX-2 follows the timing.
SUGGESTED = ("MiniMax H3 Ref2VA Pruned Turbo Lightx2v 8 Steps With Sound", "Infinitalk",
             "MiniMax H3 Ref2VA Pruned PDD 8 Steps With Sound", "LTX-2.3 Distilled 1.0 With Sound")
NOTES = (  # (model_type prefix, what to know), the first match wins
    ("minimax_h3_ref2va", "Uses each shot's character and place pictures, so performers keep their look."),
    ("infinitetalk", "A dedicated lip-sync model: the closest mouth movement, long takes; it uses the first picture."),
    ("ltx2", "Uses one picture; follows the song's timing."),
    ("minimax_h3_fl2va", "No reference pictures: performers are described in words only."),
)
MAX_LYRICS, MAX_STYLE = 3000, 500
SONG_SECONDS = (10, 240)
# What a lip-sync prompt asks for (WanGP's H3 notes: name the singer and ask for synchronized lip movement; never
# write the words, or the model voices its own and the mouth stops following the song).
LIPSYNC_WORDING = "singing with natural lip movements synchronized to the music"


class MusicVideoError(ValueError):
    pass


def note_for(model_type: str) -> str:
    return next((n for prefix, n in NOTES if model_type.startswith(prefix)), "")


def shot_seconds(frames: int) -> float:
    return (frames - 1) / 24


class MusicVideo:
    def __init__(self, studio):
        self.s = studio
        self.db = studio.db
        self.slices_dir: Path = studio.settings.media_dir / "song-slices"

    # --- the setup ----------------------------------------------------------------------------------

    async def view(self, project_id: str) -> dict:
        p = await self._project(project_id)
        try:
            matrix = await self.s.caps.matrix()
        except Exception:  # noqa: BLE001 - WanGP is down: the setup still opens, without the model list
            log.warning("music video setup: WanGP's models couldn't be listed", exc_info=True)
            matrix = []
        speech = [t for t in matrix if t["task"] in SPEECH_VIDEO_TASKS and t.get("valid") is not False]
        rank = {name: i for i, name in enumerate(SUGGESTED)}
        speech.sort(key=lambda t: (rank.get(t["template"], len(SUGGESTED)), t["template"]))
        suggested = next((t["template"] for t in speech if t["template"] in rank), speech[0]["template"] if speech else None)
        song = None
        if p["song_file_id"]:
            f = await self.db.fetchone("SELECT id, duration_s, mime FROM library_files WHERE id = ?", (p["song_file_id"],))
            song = f and {"file_id": f["id"], "duration_s": f["duration_s"], "mime": f["mime"]}
        files, candidates, jobs = [], [], []
        if p["song_asset_id"]:
            try:
                asset = await self.s.library.asset(p["song_asset_id"])
                files = [{"file_id": f["file_id"], "caption": f["caption"], "duration_s": f.get("duration_s")}
                         for v in asset["variants"] for f in v["files"] if f["kind"] == "audio"]
                gen = await self.s.images.for_asset(p["song_asset_id"])
                candidates = [c for c in gen["candidates"] if c.get("media") == "audio"]
                jobs = [j for j in gen["jobs"] if j.get("media") == "audio"][:3]
            except Exception:  # noqa: BLE001 - the song's asset was deleted: start over
                log.info("song asset %s of %s is gone", p["song_asset_id"], project_id)
        others = await self.db.fetchall(
            "SELECT f.id AS file_id, a.name, f.duration_s FROM assets a JOIN variants v ON v.asset_id = a.id "
            "JOIN variant_files vf ON vf.variant_id = v.id JOIN library_files f ON f.id = vf.file_id "
            "WHERE a.kind = 'sound' AND a.deleted_at IS NULL AND a.draft = 0 AND v.deleted_at IS NULL "
            "AND f.kind = 'audio' ORDER BY a.name LIMIT 40")
        return {
            "mode": p["music_mode"], "lyrics": p["lyrics"], "style": p["song_style"],
            "speech_template": p["speech_template"] or suggested, "song": song,
            "song_asset_id": p["song_asset_id"], "song_files": files, "song_candidates": candidates, "song_jobs": jobs,
            "library_songs": others,
            "lipsync_models": [{"template": t["template"], "model_type": t["model_type"], "installed": t.get("installed"),
                                "refs": t["refs"], "suggested": t["template"] == suggested,
                                "note": note_for(t["model_type"])} for t in speech],
            "song_models": [{"template": t["template"], "installed": t.get("installed")}
                            for t in matrix if t["task"] in MUSIC_TASKS and t.get("valid") is not False],
            "song_model": (await self.s.caps.settings())["music"],
        }

    async def update(self, project_id: str, patch: dict, actor: str) -> dict:
        p = await self._project(project_id)
        sets: dict = {}
        if "mode" in patch:
            mode = patch["mode"] or None
            if mode is not None and mode not in MODES:
                raise MusicVideoError(f"mode must be one of {', '.join(MODES)}, or empty for an ordinary film")
            sets["music_mode"] = mode
        if "lyrics" in patch:
            sets["lyrics"] = self._text(patch["lyrics"], MAX_LYRICS, "lyrics")
        if "style" in patch:
            sets["song_style"] = self._text(patch["style"], MAX_STYLE, "style")
        if "speech_template" in patch:
            name = patch["speech_template"] or None
            if name and not any(t["template"] == name and t["task"] in SPEECH_VIDEO_TASKS for t in await self.s.caps.matrix()):
                raise MusicVideoError(f"{name!r} isn't one of WanGP's song-driven video models")
            sets["speech_template"] = name
        song = None
        if "song_file_id" in patch:
            if patch["song_file_id"]:
                song = await self.db.fetchone("SELECT * FROM library_files WHERE id = ? AND kind = 'audio'",
                                              (str(patch["song_file_id"]),))
                if not song:
                    raise MusicVideoError("choose an audio file of the library as the song")
                sets["song_file_id"] = song["id"]
            else:
                sets["song_file_id"] = None
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            await self.db.execute(f"UPDATE projects SET {cols}, updated_at = ? WHERE id = ?", (*sets.values(), now(), project_id))
            await self.db.audit(actor, "project.music_video", project_id, {k: v for k, v in sets.items() if k != "lyrics"})
        if song:
            await self._song_into_edit(project_id, song, actor, p)
        if sets.get("music_mode") is None and "music_mode" in sets and p["song_file_id"]:
            pass  # back to an ordinary film: the song stays in the edit as music, as the owner may want it there
        await self.s.bus.publish("project.updated", {"project_id": project_id}, project_id)
        return await self.view(project_id)

    async def _song_into_edit(self, project_id: str, song: dict, actor: str, project: dict):
        """The song becomes the film's soundtrack: the first music track, from the start, at full level, no ducking
        (the shots are muted under it); the target length becomes the song's."""
        doc = await self.s.edit.doc(project_id)
        track = {"file_id": song["id"], "start_s": 0, "offset_s": 0, "gain_db": 0, "fade_in_s": 0, "fade_out_s": 1.0,
                 "duck": False}
        rest = [m for m in doc["music"] if m["file_id"] != song["id"] and m["file_id"] != project.get("song_file_id")]
        await self.s.edit.update(project_id, {"music": [track, *rest][:2]}, actor, note="the music video's song")
        if song["duration_s"]:
            await self.db.execute("UPDATE projects SET target_seconds = ? WHERE id = ?",
                                  (max(1, round(song["duration_s"])), project_id))

    async def generate_song(self, project_id: str, actor: str, lyrics: str | None = None, style: str | None = None,
                            seconds: float | None = None, template: str | None = None, count: int = 2) -> dict:
        """Song candidates from the lyrics and style, made by a WanGP music model on the project's song asset (a Sound
        in the Library, created on first use). The owner keeps one; it becomes the song."""
        p = await self._project(project_id)
        lyrics = self._text(p["lyrics"] if lyrics is None else lyrics, MAX_LYRICS, "lyrics")
        style = self._text(p["song_style"] if style is None else style, MAX_STYLE, "style")
        if len(style) < 3:
            raise MusicVideoError("describe the song's style (genre, mood, instruments, voice), at least a few words")
        seconds = float(seconds or p["target_seconds"] or 30)
        if not SONG_SECONDS[0] <= seconds <= SONG_SECONDS[1]:
            raise MusicVideoError(f"a song must be {SONG_SECONDS[0]}-{SONG_SECONDS[1]} s long")
        await self.db.execute("UPDATE projects SET lyrics = ?, song_style = ?, updated_at = ? WHERE id = ?",
                              (lyrics, style, now(), project_id))
        asset_id = await self._song_asset(project_id, p, actor)
        variant = next(v for v in (await self.s.library.asset(asset_id))["variants"])
        template = template or (await self.s.caps.settings())["music"]
        job = await self.s.images.submit(variant["id"], lyrics or "[Instrumental]", actor, template, count=count,
                                         label="song", media="audio",
                                         extra={"alt_prompt": style, "duration_seconds": round(seconds)})
        return {"job": job, "song_asset_id": asset_id}

    async def use_candidate(self, project_id: str, candidate_id: str, actor: str) -> dict:
        """Keep a generated song candidate in the library and make it the song."""
        c = await self.s.images.candidate(candidate_id)
        p = await self._project(project_id)
        if c["asset_id"] != p["song_asset_id"]:
            raise MusicVideoError("that candidate isn't one of this project's songs")
        await self.s.images.keep(candidate_id, {"caption": "song"}, actor)
        return await self.update(project_id, {"song_file_id": c["file_id"]}, actor)

    async def song_asset(self, project_id: str, actor: str) -> str:
        """The project's song asset (created on first use), e.g. for uploading a track to it."""
        return await self._song_asset(project_id, await self._project(project_id), actor)

    async def _song_asset(self, project_id: str, p: dict, actor: str) -> str:
        if p["song_asset_id"]:
            row = await self.db.fetchone("SELECT id FROM assets WHERE id = ? AND deleted_at IS NULL", (p["song_asset_id"],))
            if row:
                return row["id"]
        asset = await self.s.library.create_asset(
            {"kind": "sound", "name": f"{p['title'][:100]} · song", "description": "The song of the music video "
             f"“{p['title']}”", "tags": "song music-video"}, actor)
        await self.db.execute("UPDATE projects SET song_asset_id = ? WHERE id = ?", (asset["id"], project_id))
        return asset["id"]

    # --- rendering ------------------------------------------------------------------------------------

    async def for_render(self, shot: dict) -> dict | None:
        """For a lip-sync shot: {"template": the song-driven template (task, model_type), "path": its slice of the
        song, "start_s", "seconds"}; None when the shot renders as usual."""
        p = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (shot["project_id"],))
        if not p or p["music_mode"] != "lipsync" or not p["song_file_id"]:
            return None
        matrix = await self.s.caps.matrix()
        speech = [t for t in matrix if t["task"] in SPEECH_VIDEO_TASKS and t.get("valid") is not False]
        name = p["speech_template"] or next((n for n in SUGGESTED if any(t["template"] == n for t in speech)), None)
        tpl = next((t for t in speech if t["template"] == name), None)
        if not tpl:
            raise MusicVideoError("this music video lip-syncs to its song, but WanGP has no song-driven video model "
                                  f"named {name!r}; choose one in the project's Music video settings")
        start = await self.shot_start(shot)
        seconds = shot_seconds(shot["frames"])
        song = await self.s.library.file(p["song_file_id"])
        path = await self.slice(song, start, seconds)
        return {"template": {"template": tpl["template"], "model_type": tpl["model_type"], "task": tpl["task"]},
                "path": path, "start_s": round(start, 3), "seconds": round(seconds, 3), "refs": tpl["refs"]}

    async def shot_start(self, shot: dict) -> float:
        """Where the shot starts in the film: the shots before it, back to back (a music video cuts)."""
        order = await self.s.storyboard.order(shot["project_id"])
        start = 0.0
        for sid in order:
            if sid == shot["id"]:
                return start
            row = await self.db.fetchone("SELECT frames FROM shots WHERE id = ?", (sid,))
            start += shot_seconds(row["frames"])
        return start

    async def slice(self, song: dict, start: float, seconds: float) -> Path:
        """The song from start for seconds, as a WAV (cached); past the song's end it's silence."""
        key = hashlib.sha256(f"{song['sha256']}:{start:.3f}:{seconds:.3f}".encode()).hexdigest()[:24]
        out = self.slices_dir / f"{key}.wav"
        if not out.exists():
            self.slices_dir.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp.wav")
            await _run("ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(self.s.library.path(song)),
                       "-t", f"{seconds:.3f}", "-af", f"apad=whole_dur={seconds:.3f}", "-ac", "2", "-ar", "44100",
                       str(tmp))
            await asyncio.to_thread(tmp.replace, out)
        return out

    # --- helpers --------------------------------------------------------------------------------------

    async def _project(self, project_id: str) -> dict:
        p = await self.db.fetchone("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
        if not p:
            raise MusicVideoError("no such project")
        return p

    @staticmethod
    def _text(value, limit: int, field: str) -> str:
        value = str(value or "").strip()
        if len(value) > limit:
            raise MusicVideoError(f"{field} must be at most {limit} characters")
        return value
