"""A shot's sound, and the structured prompt MiniMax H3 needs to voice it.

H3 makes picture and sound together and reads a six-section prompt (WanGP models/minimax_h3/prompt_enhancer.py):
subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music.
Exact speech must be written as ``(S1) <d>[English] the words</d>``; quoted text in free prose is not a script, so
H3 improvises words (or mumbles) instead. FilmLab therefore keeps the sound as fields on the shot and compiles the
prompt itself at render time:

    sound = {"lines": [{"speaker": "Anthony_science", "text": "Dad!", "delivery": "amazed, soft", "language": ""}],
             "ambience": "servo whirr, low electrical hum", "music": "", "refine": False}

``lines == []`` means nobody speaks. A shot whose sound is None (made before this existed) renders its prompt as is.
"""

import json
import re

MAX_LINES, MAX_VOICES = 6, 3
LANGUAGES = {"english", "french", "german", "spanish", "italian", "portuguese", "dutch", "polish", "russian", "turkish",
             "arabic", "hindi", "chinese", "japanese", "korean"}
VOICE_MIN_S, VOICE_MAX_S = 2.0, 30.0  # H3 needs at least 2 s per sample and uses about 15 s in total


class SoundError(ValueError):
    pass


def _str(v, limit: int, name: str, errors: list, required=False) -> str:
    s = " ".join(str(v or "").split())
    if required and not s:
        errors.append(f"{name} is required")
    if len(s) > limit:
        errors.append(f"{name} must be at most {limit} characters")
    return s[:limit]


def clean_sound(value, errors: list | None = None) -> dict | None:
    """Normalise a shot's sound (from the owner, the director or a plan). Raises SoundError unless errors is given."""
    own = errors is None
    errors = [] if own else errors
    if value in (None, "", {}):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            errors.append("sound must be an object: {lines, ambience, music}")
            value = {}
    if not isinstance(value, dict):
        errors.append("sound must be an object: {lines, ambience, music}")
        value = {}
    lines_in = value.get("lines", value.get("dialogue")) or []
    if not isinstance(lines_in, list):
        errors.append("sound.lines must be a list of {speaker, text, delivery}")
        lines_in = []
    if len(lines_in) > MAX_LINES:
        errors.append(f"sound.lines: at most {MAX_LINES} lines per shot")
    lines = []
    for i, ln in enumerate(lines_in[:MAX_LINES]):
        if not isinstance(ln, dict):
            errors.append(f"sound.lines[{i}] must be an object {{speaker, text, delivery}}")
            continue
        lang = _str(ln.get("language"), 20, f"sound.lines[{i}].language", errors)
        if lang and lang.lower() not in LANGUAGES:
            errors.append(f"sound.lines[{i}].language must be one of {', '.join(sorted(LANGUAGES))}")
        text = _str(ln.get("text") or ln.get("line"), 300, f"sound.lines[{i}].text", errors, required=True)
        lines.append({"speaker": _str(ln.get("speaker") or ln.get("who"), 80, f"sound.lines[{i}].speaker", errors,
                                      required=True),
                      "text": text.strip("“”\"'") if text else text,
                      "delivery": _str(ln.get("delivery"), 120, f"sound.lines[{i}].delivery", errors),
                      "language": lang.capitalize() if lang else ""})
    out = {"lines": lines,
           "ambience": _str(value.get("ambience") or value.get("soundscape"), 600, "sound.ambience", errors),
           "music": _str(value.get("music"), 300, "sound.music", errors),
           "refine": bool(value.get("refine"))}
    if out["music"].lower() in ("n/a", "none", "no music", "no"):
        out["music"] = ""
    if own and errors:
        raise SoundError("; ".join(errors))
    return out


def load(raw) -> dict | None:
    if not raw:
        return None
    try:
        return clean_sound(raw, [])
    except Exception:  # noqa: BLE001 - a damaged record renders like a shot without sound fields
        return None


def is_h3(model_type: str | None) -> bool:
    return bool(model_type) and str(model_type).startswith("minimax_h3")


def _key(name: str) -> str:
    return re.sub(r"[\s_-]+", " ", str(name or "")).strip().lower()


def _subjects(refs: list[dict]) -> list[dict]:
    """One subject per library asset, in reference order; several pictures of one asset are views of it."""
    subjects: dict[str, dict] = {}
    for r in sorted(refs, key=lambda x: x["position"]):
        s = subjects.setdefault(r["asset_id"], {"asset_id": r["asset_id"], "name": r["asset_name"], "kind": r["kind"],
                                                "pictures": [], "n": len(subjects) + 1})
        s["pictures"].append(r["position"])
    return list(subjects.values())


def speaker_subject(speaker: str, subjects: list[dict]) -> dict | None:
    k = _key(speaker)
    return next((s for s in subjects if s["asset_id"] == speaker or _key(s["name"]) == k), None)


def voices_needed(sound: dict | None, refs: list[dict]) -> list[str]:
    """Asset ids of the characters who speak in this shot, in speaking order (the first MAX_VOICES get a voice)."""
    if not sound:
        return []
    subjects, out = _subjects(refs), []
    for ln in sound["lines"]:
        s = speaker_subject(ln["speaker"], subjects)
        if s and s["kind"] == "character" and s["asset_id"] not in out:
            out.append(s["asset_id"])
    return out


_DEFINE = {
    "character": "{label} is {name} from {pics}, preserving their identity, face, hair, build and clothing.",
    "location": "{label} is the setting {name} from {pics}, preserving its layout, architecture, materials and props.",
    "prop": "{label} is the {name} from {pics}, preserving its shape, colours, materials and details.",
    "style": "{label} is the visual style shown in {pics}: its palette, rendering, texture and lighting.",
}
_RETAIN = {
    "character": "fully_preserved - identity, face, hair and clothing are retained; pose, action and setting are new.",
    "location": "fully_preserved - the place's layout, look and materials are retained; the action in it is new.",
    "prop": "fully_preserved - the object's shape and look are retained.",
    "style": "attribute_transfer - palette, rendering and lighting style transfer; content does not.",
}
_PICTURE = re.compile(r"<\s*picture\s+(\d+)\s*>", re.I)
# The director's old habit: a trailing "Sound: …" sentence, now held in the sound fields instead.
_SOUND_TAIL = re.compile(r"\s*\bSound(?:s| design)?\s*:\s.*$", re.I | re.S)


def compile_h3(prompt: str, sound: dict, refs: list[dict], voices: list[dict], idea: str = "") -> str:
    """The six-section H3 prompt for a shot. voices: [{asset_id, n}] in <Audio n> order (already uploaded)."""
    if "detailed_description:" in prompt:  # the owner wrote H3's own format: send it untouched
        return prompt
    subjects = _subjects(refs)
    by_picture = {p: s for s in subjects for p in s["pictures"]}
    label = {s["asset_id"]: f"<Subject {s['n']}>" for s in subjects}
    audio = {v["asset_id"]: f"<Audio {v['n']}>" for v in voices}

    defs = []
    for s in subjects:
        pics = ", ".join(f"<Picture {p}>" for p in s["pictures"])
        defs.append(_DEFINE.get(s["kind"], _DEFINE["prop"]).format(label=label[s["asset_id"]], name=s["name"], pics=pics))
    for s in subjects:
        if s["asset_id"] in audio:
            defs.append(f"{audio[s['asset_id']]} supplies the reference for {label[s['asset_id']]}'s voice: its timbre, "
                        "accent and natural delivery, without copying its original words or background noise.")

    task = "reference generation" if subjects else "text to video"
    summary = f"[{task}{' + audio reference' if audio else ''}] {' '.join((idea or '').split())[:300] or 'One continuous shot.'}"
    retention = [f"{label[s['asset_id']]} (appears in [Shot 1]): {_RETAIN.get(s['kind'], _RETAIN['prop'])}" for s in subjects]
    retention += [f"{audio[a]} (heard in [Shot 1]): reference - voice timbre and delivery guide the new line; its words "
                  "and ambience are not copied." for a in audio]

    picture = _SOUND_TAIL.sub("", prompt).strip()
    # <Picture N> in the prose becomes the subject that picture belongs to (H3 describes subjects, not pictures)
    picture = _PICTURE.sub(lambda m: label[by_picture[int(m[1])]["asset_id"]] if int(m[1]) in by_picture else m[0], picture)
    speech, ids = [], {}
    for ln in sound["lines"]:
        s = speaker_subject(ln["speaker"], subjects)
        who = label[s["asset_id"]] if s else ln["speaker"]
        sid = ids.setdefault(s["asset_id"] if s else _key(ln["speaker"]), f"S{len(ids) + 1}")
        how = f", {ln['delivery']}," if ln["delivery"] else ""
        voice = f" in the voice referenced from {audio[s['asset_id']]}," if s and s["asset_id"] in audio else ""
        speech.append(f"{who} says{how}{voice} ({sid}) <d>[{ln['language'] or 'English'}] {ln['text']}</d>")
    if speech:
        body = " ".join(speech)
        speech_text = (f"{body} Their lips stay synchronized with the words. These are the only words spoken; nobody "
                       "else speaks.")
    else:
        speech_text = "Nobody speaks: there is no dialogue, no voice-over and no singing."
    detailed = f"{picture if picture.startswith('[Shot') else '[Shot 1] ' + picture} {speech_text}"

    ambience = sound["ambience"] or "Natural sounds of the action and the room tone of the place."
    if not sound["lines"]:
        ambience = ambience.rstrip(". ") + ". No speech, voices, murmurs or vocal sounds."
    return "\n".join([
        "subject_definitions:", *(defs or ["No reference subjects: everything is described below."]),
        "summary:", summary,
        "retention_analysis:", *(retention or ["N/A"]),
        "detailed_description:", detailed,
        f"overall_soundscape: {ambience}",
        f"non_diegetic_music: {sound['music'] or 'N/A'}",
    ])


def plain_prompt(prompt: str, sound: dict | None) -> str:
    """For audio models other than H3: the sound fields as a plain sentence after the picture."""
    if not sound:
        return prompt
    picture = _SOUND_TAIL.sub("", prompt).strip()
    said = " ".join(f"{ln['speaker']} says{' (' + ln['delivery'] + ')' if ln['delivery'] else ''}: “{ln['text']}”"
                    for ln in sound["lines"])
    parts = [said or "No dialogue, no voices.", sound["ambience"], sound["music"] and f"Music: {sound['music']}"]
    return f"{picture} Sound: " + " ".join(p.rstrip(".") + "." for p in parts if p)


def apply_h3(settings: dict, prompt: str, sound: dict, refs: list[dict], voices: list[dict], idea: str = "") -> dict:
    """WanGP settings for an H3 shot with sound fields: the compiled prompt, its voice references (A / AB / ABD with
    audio_guide 1-3) and the audio refinement phase."""
    voices = voices[:MAX_VOICES]
    out = dict(settings, prompt=compile_h3(prompt, sound, refs, voices, idea))
    if voices:
        out["audio_prompt_type"] = ("A", "AB", "ABD")[len(voices) - 1]
        for v, key in zip(voices, ("audio_guide", "audio_guide2", "audio_guide3")):
            out[key] = v["wangp_media_id"]
    # Only templates that declare it take audio_refinement (WanGP refuses an undeclared custom setting): the PDD and
    # PTD variants don't; there the option is skipped (the cockpit says so).
    if "audio_refinement" in (out.get("custom_settings") or {}):
        out["custom_settings"] = dict(out["custom_settings"], audio_refinement="enabled" if sound["refine"] else "none")
    return out
