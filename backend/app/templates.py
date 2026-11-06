"""Production templates: a starting point for a new project, and the storyboard's contact sheet with a
continuity checklist built from the takes' inspections."""

import json
import re

from .db import Database

TEMPLATES = {
    "horror_teaser": {
        "name": "Horror teaser", "target_seconds": 30, "default_frames": 121, "default_resolution": "1280x720",
        "description": "Slow dread, one sharp scare, a title at the end.",
        "brief": "Who is in danger, where and when (time of day, weather), what threatens them, and the moment of the scare: ",
        "style": "Horror teaser: slow dread building to one sharp scare. Low-key light and deep shadows, a mostly static or "
                 "creeping camera, ambient sound and a near-silence before the scare. No dialogue unless the brief asks. "
                 "End on a title card.",
    },
    "music_video": {
        "name": "Music video", "target_seconds": 30, "default_frames": 73, "default_resolution": "1280x720",
        "description": "Short shots cut to a music track from the Library.",
        "brief": "The song (add it to the Library as a Sound), the performer, the places and the visual idea: ",
        "style": "Music video: short shots of 2-4 s with strong colour and movement, the performer in most shots, varied "
                 "framing (wide, medium, close) and camera moves. The music track carries the sound: no dialogue in any "
                 "shot (write \"No dialogue, no voices\").",
    },
    "illustrated_story": {
        "name": "Illustrated story", "target_seconds": 45, "default_frames": 145, "default_resolution": "960x544",
        "description": "A storybook look with narration as captions.",
        "brief": "The story in a few sentences, its characters and the illustrated style (e.g. watercolour, ink and wash): ",
        "style": "Illustrated story: one consistent illustrated style named in every prompt, gentle camera moves, calm "
                 "pacing of about 6 s a shot. Narration goes into captions in the Edit view, not spoken lines.",
    },
    "character_dialogue": {
        "name": "Character dialogue", "target_seconds": 30, "default_frames": 121, "default_resolution": "1280x720",
        "description": "Two characters talking, shot and reverse shot.",
        "brief": "Who talks to whom, where, what they want from each other, and the lines (one per shot): ",
        "style": "Character dialogue: alternate over-the-shoulder and close-up shots; keep each character on the same side "
                 "of the frame in every shot (180-degree rule). Each shot has one spoken line: give the speaker and the "
                 "exact words in quotes, and say that nobody else speaks.",
    },
}


def listing() -> list[dict]:
    return [{"id": k, **{x: v[x] for x in ("name", "description", "target_seconds", "default_frames",
                                          "default_resolution", "brief", "style")}} for k, v in TEMPLATES.items()]


def apply(template_id: str | None) -> dict:
    """Project fields for a new project from a template (empty when none is chosen)."""
    if not template_id:
        return {}
    t = TEMPLATES.get(template_id)
    if not t:
        raise ValueError(f"unknown template; choose one of {', '.join(TEMPLATES)}")
    return {"template": template_id, "style": t["style"], "target_seconds": t["target_seconds"],
            "default_frames": t["default_frames"], "default_resolution": t["default_resolution"]}


# --- contact sheet and continuity ---------------------------------------------------------

def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", (s or "").lower()) if w not in {"with", "from", "dark", "into", "very"}}


async def contact_sheet(db: Database, storyboard, edit, project_id: str) -> dict:
    scenes = {s["id"]: s for s in await storyboard.scenes(project_id)}
    rows = []
    for sid in await storyboard.order(project_id):
        shot = await db.fetchone("SELECT * FROM shots WHERE id = ?", (sid,))
        take = await edit.chosen_take(shot)
        insp = take and await db.fetchone("SELECT * FROM take_inspections WHERE take_id = ?", (take["id"],))
        vision = json.loads(insp["vision"]) if insp and insp["vision"] else {}
        rows.append({"shot_id": sid, "position": shot["position"], "title": shot["title"], "scene_id": shot["scene_id"],
                     "seconds": round((shot["frames"] - 1) / 24, 1), "transition": shot["transition"],
                     "take_id": take and take["id"], "thumb_media_id": None,
                     "sheet": bool(insp and insp["sheet_rel"]), "inspection": insp and {
                         "verdict": insp["verdict"], "summary": insp["summary"],
                         "setting_seen": vision.get("setting_seen"),
                         "characters": [c["name"] for c in vision.get("characters") or [] if c.get("visible")]}})
    groups, checks = [], []
    for scene_id in list(dict.fromkeys(r["scene_id"] for r in rows)):
        sc = scenes.get(scene_id)
        shots_ = [r for r in rows if r["scene_id"] == scene_id]
        groups.append({"scene_id": scene_id, "title": sc["title"] if sc else "Unsorted",
                       "time_place": sc and sc["time_place"], "shots": shots_})
        for prev, cur in zip(shots_, shots_[1:]):
            a, b = prev["inspection"], cur["inspection"]
            here = f"shots {prev['position']} → {cur['position']}"
            if not a or not b:
                checks.append({"where": here, "level": "info", "text": "not inspected yet: nothing to compare"})
                continue
            wa, wb = _words(a["setting_seen"]), _words(b["setting_seen"])
            if wa and wb and len(wa & wb) / len(wa | wb) < 0.15:
                checks.append({"where": here, "level": "warn", "text": f"the setting looks different: "
                               f"“{a['setting_seen']}” then “{b['setting_seen']}”"})
            gone = [c for c in a["characters"] if c not in b["characters"]]
            new = [c for c in b["characters"] if c not in a["characters"]]
            if gone or new:
                checks.append({"where": here, "level": "info", "text": "; ".join(
                    x for x in (gone and f"{', '.join(gone)} not seen any more", new and f"{', '.join(new)} appears")
                    if x)})
        for r in shots_:
            if r["inspection"] and r["inspection"]["verdict"] == "flagged":
                checks.append({"where": f"shot {r['position']}", "level": "warn", "text": r["inspection"]["summary"]})
            if not r["take_id"]:
                checks.append({"where": f"shot {r['position']}", "level": "warn", "text": "no usable take yet"})
    return {"project_id": project_id, "scenes": groups, "checks": checks}
