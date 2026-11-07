"""What each installed WanGP template can do, FilmLab's default templates, and pre-render checks.

The matrix is built from wangp_templates (inputs, specialities) plus a CPU-only wangp_validate per
template. Reference templates are validated with FilmLab's own probe image, because WanGP refuses
to validate them without image_refs. Results are cached (kv) until refreshed: validation is cheap
but there are ~30 templates.
"""

import asyncio
import logging
import re
from pathlib import Path

from . import sound
from .db import Database, now
from .media import _run, sha256_file
from .wangp.client import WanGPClient, WanGPError

log = logging.getLogger("filmlab.capabilities")

TASKS = ("gen_video", "gen_video_with_refs", "gen_image", "edit_image", "gen_speech_from_description", "gen_song")
VIDEO_TASKS = ("gen_video", "gen_video_with_refs")
IMAGE_TASKS = ("gen_image", "edit_image")
VOICE_TASKS = ("gen_speech_from_description",)  # a voice designed from a description (the Creator)
MUSIC_TASKS = ("gen_song",)
AUDIO_TASKS = VOICE_TASKS + MUSIC_TASKS
# FilmLab's tested defaults plus WanGP's own image defaults; each can be changed in Settings.
DEFAULTS = {
    "video_refs": "MiniMax H3 Ref2VA Pruned PDD 8 Steps",
    "video_plain": "MiniMax H3 FL2VA Pruned PDD 8 Steps",
    "image_text": "Z Image Turbo",
    "image_refs": "Flux Klein 9B",
    "image_candidates": 2,
    "voice_design": "Qwen3 1.7B",
    "music": "ACE-Step 1.5 Turbo LM 1.7B",
}
SETTING_KINDS = {"video_refs": VIDEO_TASKS, "video_plain": ("gen_video",), "image_text": ("gen_image",),
                 "image_refs": IMAGE_TASKS, "voice_design": VOICE_TASKS, "music": MUSIC_TASKS}
VALIDATE_CONCURRENCY = 4


class CapabilityError(ValueError):
    pass


def ref_support(image_inputs: list[str]) -> str:
    if "multiple_references" in image_inputs:
        return "multiple"
    if "reference" in image_inputs or "single_reference" in image_inputs:
        return "single"
    return "none"


def _reason(diagnostics: list[dict]) -> str | None:
    for d in diagnostics:
        msg = d.get("message", "")
        if "LoRAs not found" in msg:
            return "a LoRA this template needs isn't installed in WanGP"
        if "requires" in msg:
            return msg
    errors = [d.get("message", "") for d in diagnostics if d.get("severity") == "error"]
    return errors[0] if errors else (diagnostics[0].get("message") if diagnostics else None)


class Capabilities:
    def __init__(self, db: Database, wangp: WanGPClient, data_dir: Path, uploader):
        self.db, self.wangp = db, wangp
        self.probe_path = data_dir / "probe-ref.png"
        self.uploader = uploader  # async (path, sha256, mime) -> wangp media id
        self._lock = asyncio.Lock()

    # --- settings -----------------------------------------------------------

    async def settings(self) -> dict:
        return DEFAULTS | (await self.db.kv_get("settings.templates", {}) or {})

    async def update_settings(self, changes: dict, actor: str) -> dict:
        current = await self.settings()
        matrix = await self.matrix()
        for key, value in changes.items():
            if key == "image_candidates":
                if not isinstance(value, int) or not 1 <= value <= 4:
                    raise CapabilityError("image_candidates must be 1-4")
                current[key] = value
                continue
            if key not in SETTING_KINDS:
                raise CapabilityError(f"unknown setting {key}")
            self.ensure_usable(matrix, value, SETTING_KINDS[key])
            current[key] = value
        await self.db.kv_set("settings.templates", current)
        await self.db.audit(actor, "settings.templates", None, changes)
        return current

    @staticmethod
    def ensure_usable(matrix: list[dict], template: str, tasks) -> dict:
        entry = next((t for t in matrix if t["template"] == template and t["task"] in tasks), None)
        if not entry:
            raise CapabilityError(f"{template!r} isn't an installed {'/'.join(tasks)} template")
        if entry["valid"] is False:
            raise CapabilityError(f"{template!r} can't be used: {entry['reason'] or 'WanGP rejects it'}")
        return entry

    # --- matrix --------------------------------------------------------------

    async def matrix(self, refresh: bool = False) -> list[dict]:
        cached = await self.db.kv_get("capabilities.matrix")
        if cached and not refresh:
            return cached["templates"]
        async with self._lock:
            cached = await self.db.kv_get("capabilities.matrix")
            if cached and not refresh:
                return cached["templates"]
            templates = await self._build()
            await self.db.kv_set("capabilities.matrix", {"built_at": now(), "templates": templates})
            return templates

    async def built_at(self) -> str | None:
        cached = await self.db.kv_get("capabilities.matrix")
        return cached and cached.get("built_at")

    async def _probe_ref(self) -> str:
        if not self.probe_path.exists():
            self.probe_path.parent.mkdir(parents=True, exist_ok=True)
            await _run("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=512x512", "-frames:v", "1",
                       str(self.probe_path))
        return await self.uploader(self.probe_path, await asyncio.to_thread(sha256_file, self.probe_path), "image/png")

    async def _build(self) -> list[dict]:
        probe = await self._probe_ref()
        rows = []
        for task in TASKS:
            try:
                listing = await self.wangp.call("wangp_templates", {"task": task})
            except WanGPError:
                if task in AUDIO_TASKS:  # an older WanGP without audio models: the rest still works
                    continue
                raise
            for t in listing.get("templates", []):
                image_inputs = (t.get("media_inputs") or {}).get("image", [])
                rows.append({
                    "task": task, "template": t["template"], "model_type": t["model_type"],
                    "wangp_default": t.get("default", False),
                    "refs": ref_support(image_inputs), "requires_refs": task == "gen_video_with_refs",
                    "start_end": "start" in image_inputs or "end" in image_inputs,
                    "audio": "audio" in (t.get("media_inputs") or {}),
                    "specialities": t.get("specialities") or [], "input_modes": t.get("input_modes") or {},
                })
        sem = asyncio.Semaphore(VALIDATE_CONCURRENCY)

        async def check(row):
            async with sem:
                try:
                    full = await self.wangp.call("wangp_templates", {"task": row["task"], "template": row["template"]})
                    settings = dict(full.get("settings") or {}, prompt="FilmLab capability check")
                    if row["task"] in VIDEO_TASKS:
                        settings["video_length"] = 17
                    if row["task"] in AUDIO_TASKS:
                        settings["duration_seconds"] = 8
                    if row["requires_refs"] or (row["task"] == "edit_image" and row["refs"] != "none"):
                        settings["image_refs"] = [probe]
                    res = await self.wangp.call("wangp_validate", {"settings": settings})
                    task = (res.get("tasks") or [{}])[0]
                    diags = [d for d in res.get("diagnostics", []) if "multiples of" not in d.get("message", "")]
                    row["audio_refinement"] = "audio_refinement" in ((full.get("settings") or {}).get("custom_settings") or {})
                    row |= {"valid": bool(res.get("valid")),
                            "installed": (task.get("availability") or {}).get("available"),
                            "reason": None if res.get("valid") else _reason(diags),
                            "default_resolution": full.get("settings", {}).get("resolution"),
                            "fps": (task.get("requested_video") or {}).get("fps")}
                    if row["valid"] and row["installed"] is False:
                        row["reason"] = "model files aren't fully downloaded; the first render downloads them"
                except WanGPError as exc:
                    row |= {"valid": False, "installed": None, "reason": str(exc)[:200]}
        await asyncio.gather(*(check(r) for r in rows))
        return rows

    # --- per shot ----------------------------------------------------------------

    async def effective_template(self, shot: dict, project: dict, ref_count: int) -> str:
        if shot.get("template_explicit", 1):
            return shot["template"]
        s = await self.settings()
        key = "video_refs" if ref_count else "video_plain"
        return project.get(f"{key}_template") or s[key]

    async def setting_for(self, shot: dict) -> str:
        """The time and place of the shot's scene, which its prompt has to state."""
        if not shot.get("scene_id"):
            return ""
        row = await self.db.fetchone("SELECT time_place FROM scenes WHERE id = ? AND deleted_at IS NULL", (shot["scene_id"],))
        return (row and row["time_place"]) or ""

    async def checks(self, template: str, ref_count: int, prompt: str = "", setting: str = "",
                     sound_fields: dict | None = None, refs: list[dict] | None = None,
                     voiced: list[dict] | None = None) -> list[dict]:
        """Problems to report before a render: level 'block' stops it, 'warn' asks first."""
        issues = []
        matrix = await self.matrix()
        entry = next((t for t in matrix if t["template"] == template and t["task"] in VIDEO_TASKS), None)
        if not entry:
            return [{"level": "warn", "message": f"{template!r} isn't in the capability matrix; refresh it in Settings"}]
        if entry["valid"] is False:
            issues.append({"level": "block", "message": f"{template} can't be used: {entry['reason']}"})
        if ref_count and entry["refs"] == "none":
            issues.append({"level": "block", "message": f"{template} takes no reference images; remove the "
                                                         f"{ref_count} reference(s) or pick a reference template"})
        if ref_count > 1 and entry["refs"] == "single":
            issues.append({"level": "warn", "message": f"{template} uses a single reference; only Picture 1 is used"})
        if not ref_count and entry["requires_refs"]:
            issues.append({"level": "block", "message": f"{template} needs at least one reference image"})
        if entry.get("installed") is False and entry["valid"] is not False:
            issues.append({"level": "warn", "message": f"{template}: model files aren't fully downloaded; "
                                                        "the render will download them first"})
        if not entry.get("audio"):
            speech = []
        elif sound.is_h3(entry["model_type"]):
            speech = _h3_sound_issues(sound_fields, refs or [], voiced or [])
            if sound_fields and sound_fields["refine"] and entry.get("audio_refinement") is False:
                speech.append({"level": "info", "message": f"{template} has no audio refinement pass, so Refine audio "
                                                           "is skipped for this shot"})
        else:
            speech = _speech_issues(sound.plain_prompt(prompt, sound_fields) if sound_fields else prompt)
        return issues + _picture_issues(prompt, ref_count) + _setting_issues(prompt, setting) + speech

    @staticmethod
    def summary(matrix: list[dict]) -> dict:
        return {"usable": sum(1 for t in matrix if t["valid"] and t.get("installed")),
                "total": len(matrix)}


# Each shot is generated on its own: the model knows nothing of the scene unless the prompt says WHERE and WHEN.
# A scene's time and place is usually "Night, Suburban Street" or "Nirvana_Gym, indoor, dim lighting with strong
# spotlights and haze": the time of day and the place must be in the prompt; descriptive extras (light, weather,
# mood) are the prompt's own business, so they are never asked for word by word.
_FILLER = {"with", "from", "into", "near", "the", "and", "during", "late", "early", "very", "its", "his", "her", "their"}
_TIME = {"night", "nighttime", "midnight", "dawn", "dusk", "sunrise", "sunset", "morning", "noon", "midday",
         "afternoon", "evening", "day", "daytime", "daylight", "twilight"}
_SAME = [  # words a prompt may use for the same thing
    {"indoor", "indoors", "interior", "inside"}, {"outdoor", "outdoors", "exterior", "outside", "open air"},
    {"night", "nighttime", "midnight", "dark", "moonlit", "moonlight"}, {"day", "daytime", "daylight", "midday", "noon", "sunlit"},
    {"dawn", "sunrise", "first light"}, {"dusk", "sunset", "twilight", "golden hour"}, {"morning", "sunrise"},
    {"evening", "dusk", "sunset"},
]
_LONG = 4  # a part of the setting with more words than this describes it rather than names the place
# light, weather and mood: description of the setting, not the place itself
_DESCRIBES = {"light", "lights", "lighting", "lit", "dim", "bright", "glow", "glowing", "haze", "hazy", "fog", "foggy",
              "mist", "misty", "rain", "rainy", "snow", "snowy", "smoke", "smoky", "neon", "spotlight", "spotlights",
              "shadow", "shadows", "warm", "cold", "moody", "flickering", "candlelit", "overcast", "sunny", "cloudy",
              "storm", "stormy", "wind", "windy", "colour", "color", "colours", "colors", "atmosphere", "atmospheric"}


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in _FILLER]


def _mentions(word: str, text: str) -> bool:
    forms = {word} | next((g for g in _SAME if word in g), set())
    if len(word) >= 6:
        forms.add(word[:5])  # lighting ~ lights, spotlights ~ spotlight
    return any(re.search(rf"\b{re.escape(f)}", text) for f in forms)


def _setting_issues(prompt: str, setting: str) -> list[dict]:
    text = re.sub(r"[_\-]+", " ", (prompt or "").lower())
    parts = [p for p in re.split(r"[,;]|\s+[-–—]\s+", setting or "") if _words(p)]
    missing_when, place = [], []
    for part in parts:
        words = _words(part)
        times = [w for w in words if w in _TIME]
        if times:  # the time of day: say it, or a word for it ("Sunset/Night": either will do)
            if not any(_mentions(w, text) for w in times):
                missing_when += times
            continue
        if len(words) > _LONG or any(w in _DESCRIBES for w in words):
            continue  # description, not the place
        place.append(words)
    # the place: named, or in other words ("Nirvana_Gym, indoor": the gym is enough; "interior" counts for indoor)
    missing_where = [] if not place or any(_mentions(w, text) for ws in place for w in ws) else [w for ws in place for w in ws]
    missing = list(dict.fromkeys(missing_when + missing_where))
    if not missing:
        return []
    what = "where and when" if missing_when and missing_where else "when" if missing_when else "where"
    return [{"level": "warn", "message": f"The prompt doesn't say {what} the scene is ({', '.join(missing)} "
                                         f"from “{setting.strip()}”). Every shot is generated on its own, so the model "
                                         "only knows the setting if the prompt states it. ✨ Enhance can rewrite it"}]


_NO_SPEECH = re.compile(r"\b(no|without|zero)\s+(spoken\s+)?(dialogue|dialog|speech|talking|voices?|words|lines)\b"
                        r"|\bno(body| one)\s+(speaks|talks|says)\b|\bsilent(ly)?\b|\bwordless(ly)?\b", re.I)
_QUOTED = re.compile(r"[“\"][^”\"]{2,}[”\"]")


def _speech_issues(prompt: str) -> list[dict]:
    """Audio models invent mumbled speech when a person is on screen and the prompt doesn't say otherwise."""
    if _NO_SPEECH.search(prompt or "") or _QUOTED.search(prompt or ""):
        return []
    return [{"level": "warn", "message": "This model makes the sound too and may invent speech. Say “No dialogue, no "
                                         "voices” in the sound part, or give the exact line in quotes. ✨ Enhance can "
                                         "rewrite it"}]


def _h3_sound_issues(sound_fields: dict | None, refs: list[dict], voiced: list[dict]) -> list[dict]:
    """H3 voices exactly what the shot's Sound says (FilmLab writes it into H3's prompt format)."""
    if sound_fields is None:
        return [{"level": "warn", "message": "This shot's Sound isn't set, so the model may invent speech or mumble. "
                                             "In the shot's Sound, choose Nobody speaks or add who says which exact "
                                             "words. ✨ Enhance can fill it in"}]
    out = []
    subjects = sound._subjects(refs) if refs else []
    for ln in sound_fields["lines"]:
        if refs and not sound.speaker_subject(ln["speaker"], subjects):
            out.append({"level": "warn", "message": f"“{ln['speaker']}” speaks but isn't one of the shot's references, so "
                                                    "the model has to guess who that is (fine for a voice off screen)"})
    speakers = sound.voices_needed(sound_fields, refs)
    if len(voiced) > sound.MAX_VOICES:
        out.append({"level": "warn", "message": f"{len(voiced)} speakers have a voice sample; the model takes "
                                                f"{sound.MAX_VOICES}, so only the first {sound.MAX_VOICES} keep theirs"})
    elif speakers and len(voiced) < len(speakers):
        names = [s["name"] for s in sound._subjects(refs) if s["asset_id"] in set(speakers) - {v["asset_id"] for v in voiced}]
        out.append({"level": "info", "message": f"No voice sample for {', '.join(names)}: the model picks a voice. Add "
                                                "one to the character in the Library to keep it the same in every shot"})
    return out


_PICTURE = re.compile(r"<\s*picture\s+(\d+)\s*>", re.I)


def _picture_issues(prompt: str, ref_count: int) -> list[dict]:
    """The prompt's <Picture N> names must match the shot's references."""
    named = sorted({int(n) for n in _PICTURE.findall(prompt or "")})
    if named and named[-1] > ref_count:
        have = f"only {ref_count} reference{'s' if ref_count != 1 else ''}" if ref_count else "no references"
        return [{"level": "warn", "message": f"The prompt names <Picture {named[-1]}> but the shot has {have}"}]
    if ref_count and not named:
        return [{"level": "warn", "message": "The prompt doesn't name its references (<Picture 1>, …); the model "
                                             "follows them better when it does. ✨ Enhance can rewrite it"}]
    return []
