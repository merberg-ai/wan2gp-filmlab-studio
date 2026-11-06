"""The "Plan with director" brief: the owner's brief, the library (with image ids), the models and the
current storyboard in one message, so the director can answer with a single plan_storyboard call."""

from .enhance import SETTING_RULE, SOUND_RULE

MAX_ASSETS, MAX_IMAGES = 40, 3


async def library_catalogue(s) -> list[str]:
    lines = []
    for a in (await s.library.search("", None, limit=MAX_ASSETS)):
        full = await s.library.asset(a["id"])
        desc = " ".join((full["description"] or "").split())[:160]
        lines.append(f"- {full['name']} ({full['kind']}){': ' + desc if desc else ''}")
        for v in full["variants"]:
            images = [f for f in v["files"] if f["kind"] == "image"][:MAX_IMAGES]
            if images:
                ids = ", ".join(f"{f['file_id']}" + (f" ({f['caption']})" if f["caption"] else "") for f in images)
                lines.append(f"    look “{v['name']}” v{v['version']}{' (locked)' if v['locked'] else ''}: {ids}")
    return lines or ["- (the library is empty: plan shots without references, and request_image what you need)"]


async def storyboard_summary(s, project_id: str) -> list[str]:
    scenes = {x["id"]: x for x in await s.storyboard.scenes(project_id)}
    lines = []
    for sid in await s.storyboard.order(project_id):
        sh = await s.db.fetchone("SELECT * FROM shots WHERE id = ?", (sid,))
        takes = (await s.db.fetchone("SELECT COUNT(*) AS n FROM takes WHERE shot_id = ?", (sid,)))["n"]
        scene = scenes.get(sh["scene_id"])
        state = ", ".join(filter(None, [sh["status"], "locked" if sh["locked"] else "", f"{takes} take(s)" if takes else ""]))
        lines.append(f"- shot {sh['position']} “{sh['title']}” [{state}]"
                     f"{' in scene “' + scene['title'] + '” (scene_id ' + scene['id'] + ')' if scene else ''}")
    return lines


async def plan_brief(s, project: dict, replace: bool) -> str:
    cfg = await s.caps.settings()
    with_refs = project["video_refs_template"] or cfg["video_refs"]
    without = project["video_plain_template"] or cfg["video_plain"]
    target = f"{project['target_seconds']} s" if project["target_seconds"] else "not set (aim for 20-30 s)"
    current = await storyboard_summary(s, project["id"])
    lines = [
        "Plan the storyboard for this project from my brief.",
        f"My brief: “{project['brief'].strip()}”",
        *([f"Style for this film (follow it in every shot): {project['style'].strip()}"] if project.get("style") else []),
        f"Target length: {target}. New shots default to {project['default_resolution']} and "
        f"{round((project['default_frames'] - 1) / 24, 1)} s.",
        f"Video models: shots with references use {with_refs}; shots without use {without}.",
        "Library (refs take these image file_ids):",
        *await library_catalogue(s),
    ]
    if current:
        lines += ["Current storyboard:", *current]
    lines += [
        "Plan the film in parts, in film order: each plan_storyboard call holds ONE or TWO scenes (at most 6 shots); "
        "a call with a whole long film is too much to write at once and fails. After each part is saved FilmLab asks "
        "for the next. A scene has title, goal, time_place and mood, and its shots. Per shot: title; idea (one plain line of what happens); prompt; seconds (2-10, the total near the "
        "target); refs (image file_ids, main character first, then the setting; usually one image per asset, at most 4); "
        "transition into the next shot (cut unless another one helps the story); rationale (why this shot, these "
        "references and this length); sound. The prompt is the picture only: it names every reference as <Picture N> "
        "in refs order, places each character in the frame with an action and a few words on their look, and adds "
        "camera, light and mood.",
        SETTING_RULE.replace("this one prompt", "one shot's prompt"),
        SOUND_RULE,
        "If plan_storyboard returns errors, nothing of that part was written: fix exactly those and call it again.",
        ("Set replace_drafts: true in the FIRST part only. Your plan replaces the unlocked draft shots without takes; "
         "locked, approved and rendered shots stay as they are (the tool lists them). To add shots to an existing "
         "scene, give its scene_id."
         if replace else
         "Don't set replace_drafts: existing shots stay, and your scenes are added after them."),
        "If something the story needs isn't in the library, plan the shot without it and request_image it. "
        "Don't request a render. When the whole brief is planned, reply starting with “All planned:” and a short "
        "summary: scenes, shots, total length, and anything missing.",
    ]
    return "\n".join(lines)
