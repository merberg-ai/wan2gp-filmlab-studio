"""The "Enhance" brief: one structured request to the director to turn an owner's rough
shot prompt into a full video prompt that names the shot's references as <Picture N>,
picking references from the library when the shot has none. It goes through the normal
chat, so the owner sees the request and every tool call, and the director's usual limits
apply (it can edit draft shots and ask for images, never generate or render).
"""

import json

from .. import shots


# Every shot is rendered alone; these two rules keep a film looking and sounding like one film.
SETTING_RULE = ("The video model sees only this one prompt, never the other shots or the scene: state the setting in "
                "every prompt (place, time of day, weather and light), even when the shot is a close-up.")
SOUND_RULE = ("The model makes the sound too, and invents mumbled speech when a person is on screen unless told exactly "
              "what is said. Give every shot a sound: lines lists every spoken line with its speaker (the character's "
              "library name), the exact words only, and how it is said; lines: [] when nobody speaks. ambience lists "
              "the sounds of the place and the action; music only if the film has a score there. Keep speech out of "
              "the prompt: FilmLab writes the sound into the model's own format, so the words are spoken exactly. "
              "Keep lines short enough to say in the shot's length (about 2-3 words a second).")


def enhance_brief(shot: dict, refs: list[dict], cast: list[dict], scene: dict | None = None, story: str = "",
                  style: str = "", fresh: bool = False) -> str:
    """fresh: the project doesn't use the shared Library (plan_library 'none'): no references are picked from it."""
    """Built from the owner's idea, never from an earlier rewrite, so each Enhance starts clean."""
    seconds = round((shot["frames"] - 1) / 24, 1)
    lines = [
        f"Enhance shot {shot['position']} “{shot['title']}” (shot_id {shot['id']}).",
        f"My idea for it: “{shot.get('idea') or shot['prompt']}”",
        f"It is {seconds} s at {shot['resolution']}.",
    ]
    if scene:
        where = "; ".join(x for x in (scene.get("time_place"), scene.get("mood") and f"mood: {scene['mood']}") if x)
        lines.append(f"It is in scene “{scene['title']}”{': ' + where if where else ''}.")
    if story:
        lines.append(f"The film: “{' '.join(story.split())[:400]}”")
    if style:
        lines.append(f"Style for this film: {' '.join(style.split())[:600]}")
    if shot.get("sound"):
        lines.append(f"Its sound now (keep my lines and words unless they clearly don't fit, and say so if you change "
                     f"them): {shot['sound'] if isinstance(shot['sound'], str) else json.dumps(shot['sound'])}")
    if refs:
        lines.append("Its references, in order:")
        lines += _legend(refs)
        lines.append("Keep these references; don't reassign them unless one clearly doesn't fit, and say so if so.")
        if sum(1 for r in {x["asset_id"]: x for x in refs}.values() if r["kind"] == "character") > 1:
            lines.append("Every character must be clearly visible: give each one a place in the frame (left, right, "
                         "foreground, background) and an action of its own, with a few words on how they look, so "
                         "the model can tell them apart. A character mentioned only in passing gets left out.")
    elif fresh:
        lines.append("It has no references, and this film doesn't use the Library (my choice: a fresh start): don't "
                     "look for or assign library assets, and take nothing from my other projects. Describe every "
                     "character and place fully in the prompt instead.")
    else:
        names = ", ".join(f"{a['name']} ({a['kind']})" for a in cast) or "none yet"
        lines += [
            f"It has no references yet. Cast of this project: {names}.",
            f"Use list_assets with shot_id {shot['id']} (ranked for this shot, with the look the scene already uses) and "
            "get_asset to find library assets that fit what I wrote (only ones that truly fit this film's story and "
            "style: the library holds assets from all my projects), and assign_refs them "
            "(most important first: the main character, then the setting; at most 4, one image each unless a second "
            "angle really helps). If something the shot needs isn't in the library, you may request_image for it "
            "(I approve it in the Library) and say what you asked for.",
        ]
    lines += [
        "Then write the prompt from scratch with update_shot, from my idea and the references above (ignore the "
        "shot's current prompt: it's an earlier version). Keep my intent, name every reference by its picture "
        "(e.g. “<Picture 1> crouches in <Picture 2>”), and add subject, action, camera, light and mood in one or "
        "two dense paragraphs; pass the shot's sound in the same update_shot call. Don't change the length, size "
        "or template, and don't request a render.",
        SETTING_RULE,
        SOUND_RULE,
        "In update_shot, give a one-line rationale (it goes into the shot's history).",
        "Reply with the new prompt and a one-line summary of what you changed.",
    ]
    return "\n".join(lines)


def _legend(refs: list[dict]) -> list[str]:
    """One line per asset: several pictures of the same asset are views of ONE subject, not extra people."""
    groups: dict[str, list[dict]] = {}
    for r in refs:
        groups.setdefault(r["asset_id"], []).append(r)
    out = []
    for rs in groups.values():
        r = rs[0]
        pics = ", ".join(f"<Picture {x['position']}>" for x in rs)
        line = f"- {pics} = {r['asset_name']} ({r['kind']}) · {r['variant_name']} v{r['version']}"
        if (desc := " ".join(str(r.get("asset_description") or "").split())[:160]):
            line += f": {desc}"
        if len(rs) > 1:
            line += (f" — {len(rs)} views of the SAME {r['kind']}: it appears once. Name it once, e.g. "
                     f"“{r['asset_name']} ({pics})”, and never describe these pictures as separate people or places.")
        out.append(line)
    return out


def can_enhance(shot: dict) -> bool:
    return shot["status"] in shots.EDITABLE
