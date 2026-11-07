"""Before planning: what in the brief disagrees with the project's settings, with a one-click fix where there is one.

Found on a real project: the brief asked for 90-120 seconds while the target said 60, so the director planned 60;
its "Illustrated story" style asked for calm pacing and gentle camera moves while the brief had a firefight. The
director follows the settings, so a mismatch is quietly planned the wrong way: say so before planning instead.
"""

import re

from .templates import TEMPLATES

# "a 90–120 second short", "2-minute film", "about 45 seconds long", "runtime: 1.5 min"
_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"(seconds?|secs?|s|minutes?|mins?|m)\b"
_LENGTH = re.compile(rf"(?<![\d.])(?:{_NUM}\s*(?:-|–|—|to)\s*)?{_NUM}\s*-?\s*{_UNIT}", re.I)
_FILM_WORDS = re.compile(r"\b(short|film|movie|video|trailer|teaser|clip|animation|animated|story|piece|reel|runtime|"
                         r"length|long|total|duration|overall|in all)\b", re.I)
_PER_SHOT = re.compile(r"\b(each|every|per|shot|shots|scene|scenes|cut|cuts)\b", re.I)

ACTION = ("fight", "fights", "fighting", "firefight", "gunfire", "gunfight", "shootout", "battle", "combat", "chase",
          "chased", "explosion", "explodes", "exploding", "ambush", "attack", "attacks", "violent", "escape", "escapes",
          "collapse", "collapsing", "frantic", "intense", "action", "raid", "brawl", "crash")
NO_SPEECH = re.compile(r"\bno (dialogue|dialog|speech|talking|lines|voices?)\b|\bwithout (dialogue|words|speech)\b|"
                       r"\bnobody (speaks|talks)\b|\bsilent film\b", re.I)
SPEECH = re.compile(r"\b(says|said|asks|replies|shouts|whispers|tells)\b[^.]{0,40}[\"“]", re.I)
# Style phrases that pace a whole film slowly (the templates' own wording).
CALM = re.compile(r"\bcalm pacing\b|\bgentle camera moves\b|\bslow dread\b|\bmostly static\b", re.I)
LIBRARY_NAME = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")

# An alternative for an action brief on the calm storybook style: the same look, pacing that follows the action.
ACTION_STYLE = ("Illustrated action story: one consistent illustrated style named in every prompt. Wide establishing "
                "shots of 5-6 s with slow camera moves for travel and quiet moments; quicker 2-4 s shots with dynamic "
                "camera (tracking, handheld, whip pans, low angles) for action. Narration, if any, goes into captions "
                "in the Edit view, not spoken lines.")


def brief_seconds(brief: str) -> tuple[int, str] | None:
    """The film length the brief asks for, in seconds (a range gives its middle), and the words that said so."""
    for m in _LENGTH.finditer(brief or ""):
        lo, hi, unit = m.group(1), m.group(2), m.group(3).lower()
        if unit in ("s", "m") and re.fullmatch(r"1[89]\d0|20\d0", hi.replace(",", "")):
            continue  # "1980s": a decade, not a length
        around = brief[max(0, m.start() - 40): m.end() + 40]
        after = brief[m.end(): m.end() + 30]
        if _PER_SHOT.search(after[:20]) or re.search(r"\b(each|every|per)\b", brief[max(0, m.start() - 20): m.start()], re.I):
            continue  # "6 s a shot", "each shot 4 seconds": a shot's length
        if not (_FILM_WORDS.search(around) or unit.startswith("m")):
            continue
        scale = 60 if unit.startswith("m") else 1
        vals = [float(x.replace(",", ".")) * scale for x in (lo, hi) if x]
        secs = sum(vals) / len(vals)
        if 5 <= secs <= 1800:
            return int(5 * round(secs / 5)), m.group(0).strip()
    return None


def _hits(words: tuple[str, ...], text: str) -> list[str]:
    low = (text or "").lower()
    return [w for w in words if re.search(rf"\b{w}\b", low)]


def check(project: dict, assets: list[dict]) -> list[dict]:
    """Each issue: id, level (warn | info), message, and fixes [{label, patch}] (a project PATCH; none keeps it)."""
    brief, style = project.get("brief") or "", project.get("style") or ""
    issues: list[dict] = []

    asked = brief_seconds(brief)
    target = project.get("target_seconds") or 0
    if asked and (not target or abs(asked[0] - target) / max(asked[0], 1) > 0.2):
        secs, words = asked
        issues.append({
            "id": f"length:{secs}:{target}", "level": "warn",
            "message": (f"Your brief asks for about {secs} s (“{words}”), but the target length is {target} s, and the "
                        "director plans to the target." if target
                        else f"Your brief asks for about {secs} s (“{words}”), but no target length is set."),
            "fixes": [{"label": f"Set the target to {secs} s", "patch": {"target_seconds": secs}}]})

    action = _hits(ACTION, brief)
    calm = CALM.search(style)
    if calm and len(action) >= 2:
        fixes = []
        if project.get("template") == "illustrated_story" or style.startswith(TEMPLATES["illustrated_story"]["style"][:30]):
            fixes.append({"label": "Use the illustrated action style", "patch": {"style": ACTION_STYLE}})
        issues.append({
            "id": "style:action", "level": "warn",
            "message": (f"Your brief has action ({', '.join(action[:4])}), but the project's style asks for "
                        f"“{calm.group(0).lower()}”, which the director follows in every shot."),
            "fixes": fixes})

    if NO_SPEECH.search(brief) and re.search(r"\bone spoken line\b|\bexact words\b", style, re.I):
        issues.append({"id": "style:speech", "level": "warn",
                       "message": "Your brief says nobody speaks, but the project's style gives every shot a spoken line.",
                       "fixes": [{"label": "Clear the style", "patch": {"style": ""}}]})
    elif SPEECH.search(brief) and re.search(r"\bno dialogue\b", style, re.I):
        issues.append({"id": "style:speech", "level": "warn",
                       "message": "Your brief has spoken lines, but the project's style says no dialogue in any shot.",
                       "fixes": [{"label": "Clear the style", "patch": {"style": ""}}]})

    by_name = {_key(a["name"]): a for a in assets}
    missing, bare = [], []
    for name in dict.fromkeys(LIBRARY_NAME.findall(brief)):
        a = by_name.get(_key(name))
        if not a:
            missing.append(name)
        elif a["kind"] in ("character", "location", "prop") and not a.get("cover_file_id"):
            bare.append(name)
    if missing:
        issues.append({"id": "library:missing:" + ",".join(missing), "level": "warn",
                       "message": (f"Not in the Library: {', '.join(missing)}. The director plans those shots without "
                                   "references (so the look can change from shot to shot). Add them in the Library or "
                                   "with the Creator first, or check the spelling."), "fixes": []})
    if bare:
        issues.append({"id": "library:bare:" + ",".join(bare), "level": "info",
                       "message": f"No reference images yet: {', '.join(bare)}. Shots with them can't keep their look.",
                       "fixes": []})
    return issues


def _key(name: str) -> str:
    return re.sub(r"[\s_\-]+", "_", str(name or "").strip().lower())
