"""Brief coverage: when the director says the plan is done, FilmLab checks that every part of the brief is in it.

A small director writes good shots but slips on bookkeeping: live, a 60 s plan went from the warlord's entrance
straight to the escape, and the battle the brief asked for between them was never planned. Telling it to "cover
every scene" doesn't help; small questions with a forced JSON answer do (as in castpass.py):

1. once: "list what this brief says must happen, in story order" (the beats);
2. per beat: "which planned shot comes closest to showing it?", then, about that one shot, "does it show it?".

A beat no shot shows is a gap. Neighbouring gaps are asked for together, each with the shot it belongs after (the
last shot before the next beat that is shown, at a scene's end when there is one), so the chat can ask the director
for just that and put the new shots in film order. Nothing is checked for a music video (its brief is the song), a
model that isn't on Ollama, or when a question fails: the plan then stands as written."""

import logging

from . import castpass, shots

log = logging.getLogger("filmlab.coverage")
MAX_BEATS, MAX_GAPS = 12, 3
MIN_BRIEF = 60  # a one-line brief is one beat: nothing to compare


def _beats_schema() -> dict:
    return {"type": "object", "required": ["beats"], "properties": {
        "beats": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_BEATS}}}


def _closest_schema(numbers: list[int]) -> dict:
    return {"type": "object", "required": ["closest_shot", "shows_it"], "properties": {
        "closest_shot": {"type": "integer", "enum": numbers},
        "shows_it": {"type": "string", "enum": ["yes", "partly", "no"]}}}


def _judge_schema() -> dict:
    return {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}}}


def beats_question(brief: str) -> str:
    return "\n".join([
        "The brief of a short film:", f"“{brief}”",
        "List what the film must show, in story order, as separate beats: one short sentence each, in the brief's "
        f"own words, one beat per event (an arrival, a fight and an escape are three beats). At most {MAX_BEATS}. "
        "Only events the brief names: leave out its notes on style, length, camera, music and cast, and add nothing.",
    ])


def shot_lines(rows: list[dict], scenes: dict) -> list[str]:
    lines, scene_id = [], object()
    for r in rows:
        if r["scene_id"] != scene_id:
            scene_id = r["scene_id"]
            sc = scenes.get(scene_id)
            lines.append(f"Scene “{sc['title']}”:" if sc else "No scene:")
        idea = " ".join(str(r.get("idea") or "").split())[:160]
        picture = " ".join(str(r.get("prompt") or "").split())[:220]
        lines.append(f"{r['position']}. “{r['title']}”{': ' + idea if idea else ''} [picture: {picture}]")
    return lines


def closest_question(beat: str, lines: list[str]) -> str:
    return "\n".join([
        "A film is planned from a brief. One event the brief asks for:", f"“{beat}”",
        "The planned shots, in film order:", *lines,
        "Pick the one shot that comes closest to showing this event (closest_shot). Then judge it: does that shot "
        "show the event itself happening (yes), only a part of it (partly), or not at all, e.g. it only shows what "
        "happens before or after (no)?",
    ])


def judge_question(beat: str, shot: dict) -> str:
    return "\n".join([
        "One event from a film's brief:", f"“{beat}”", "One planned shot of the film:",
        f"“{shot['title']}”: {' '.join(str(shot.get('idea') or '').split())} "
        f"[picture: {' '.join(str(shot.get('prompt') or '').split())[:400]}]",
        "Does this shot show that event, or a part of it, happening on screen? no: it shows something else, e.g. "
        "what happens before or after it.",
    ])


def gaps(beats: list[str], covered: list[list[int]], rows: list[dict]) -> list[dict]:
    """The beats no shot shows, neighbours together: {"beats", "after" (the shot row they belong after; None: at
    the start), "boundary" (that place is between two scenes, or at either end of the film)}."""
    if not any(covered) or not rows:
        return []  # nothing matched at all: the answers can't be trusted, so nothing is asked for
    last_of_scene = {r["position"] for i, r in enumerate(rows)
                     if i + 1 == len(rows) or rows[i + 1]["scene_id"] != r["scene_id"]}
    by_pos = {r["position"]: r for r in rows}
    out, i = [], 0
    while i < len(beats):
        if covered[i]:
            i += 1
            continue
        j = i
        while j < len(beats) and not covered[j]:
            j += 1
        prev = next((covered[k] for k in range(i - 1, -1, -1) if covered[k]), None)
        if j == len(beats):  # the brief's ending: after the last shot
            after = rows[-1]["position"]
        else:
            a, b = (max(prev) if prev else 0), min(covered[j])
            ends = [p for p in range(a, b) if p in last_of_scene]
            after = ends[-1] if ends else b - 1  # a scene's end between the two beats, else just before the next
        out.append({"beats": beats[i:j], "after": by_pos.get(after),
                    "boundary": after == 0 or after in last_of_scene})
        i = j
    return out


async def applies(studio, project_id: str, model: str | None) -> bool:
    p = await studio.db.fetchone("SELECT brief, music_mode FROM projects WHERE id = ?", (project_id,))
    return bool(p and model and str(model).startswith("ollama/") and not p["music_mode"]
                and len((p["brief"] or "").strip()) >= MIN_BRIEF)


async def check(studio, project_id: str, model: str | None) -> dict | None:
    """{"beats": [...], "shots": [[numbers showing each beat]], "gaps": [...]}, or None when it couldn't be checked."""
    if not await applies(studio, project_id, model):
        return None
    project = await studio.db.fetchone("SELECT brief FROM projects WHERE id = ?", (project_id,))
    rows = [await shots.get_shot(studio.db, sid) for sid in await studio.storyboard.order(project_id)]
    if len(rows) < 2:
        return None
    url = (await studio.health.settings())["ollama_url"]
    transport = getattr(studio.gpu_share, "_transport", None)
    answer = await castpass.ask(url, model, beats_question(project["brief"].strip()), _beats_schema(), transport)
    beats = list(dict.fromkeys(" ".join(str(b).split())[:300] for b in (answer or {}).get("beats") or []
                               if str(b).strip()))[:MAX_BEATS]
    if len(beats) < 2:
        return None
    scenes = {s["id"]: s for s in await studio.storyboard.scenes(project_id)}
    lines, numbers = shot_lines(rows, scenes), [r["position"] for r in rows]
    by_pos = {r["position"]: r for r in rows}
    covered = []
    for beat in beats:
        # Asked for every shot that shows a beat, with a yes/no beside it, the 9B model contradicted itself (live:
        # three planned parts called missing). Picking the closest shot, then judging that one shot alone, was right
        # on every beat of the same plan.
        pick = await castpass.ask(url, model, closest_question(beat, lines), _closest_schema(numbers), transport)
        shot = by_pos.get((pick or {}).get("closest_shot"))
        if not shot:
            return None
        verdict = await castpass.ask(url, model, judge_question(beat, shot), _judge_schema(), transport)
        if verdict is None:
            return None
        covered.append([shot["position"]] if verdict.get("answer") == "yes" else [])
    log.info("brief coverage for %s: %s", project_id, list(zip(beats, covered)))
    return {"beats": beats, "shots": covered, "gaps": gaps(beats, covered, rows)}
