"""The "Plan with director" brief: the owner's brief, the library (with image ids), the models and the
current storyboard in one message, so the director can answer with a single plan_storyboard call."""

from .. import refnames
from .enhance import SETTING_RULE, SOUND_RULE

MAX_ASSETS, MAX_IMAGES = 40, 3
# What a planning turn may call: write the plan, look things up, fix a shot, ask for a missing image. No renders,
# edits or takes (fewer tool descriptions also leave a small model more room for the brief).
PLAN_TOOLS = ["plan_storyboard", "get_project", "list_assets", "get_asset", "update_shot", "update_scene",
              "request_image"]


async def library_catalogue(s) -> list[str]:
    """Each asset by name with its views, the way prompts name them ({Name} or {Name/view}): no image ids to copy
    (a small model mangles 32-character ids; refnames.py turns the names into pictures)."""
    lines = []
    for a in (await s.library.search("", None, limit=MAX_ASSETS)):
        full = await s.library.asset(a["id"])
        desc = " ".join((full["description"] or "").split())[:160]
        imgs = await refnames._images(s.db, full["id"])
        if not imgs:
            continue
        views = ", ".join(f"/{i['caption']}" if i["caption"] else f"/{n}" for n, i in enumerate(imgs[:MAX_IMAGES], 1))
        lines.append(f"- {{{full['name']}}} ({full['kind']}){': ' + desc if desc else ''} · views: {views}")
    return lines or ["- (the library is empty: plan shots without references, and request_image what you need)"]


# The Library is shared by every project: without this, a new film tends to be built from whatever earlier films
# left there (their characters, places and props) instead of from its own brief.
LIBRARY_FIT = ("Library (shared by all my projects; name an asset in a prompt as written here, e.g. {Name} or "
               "{Name/view}). Use an asset only when my brief "
               "names it or clearly calls for exactly that character, place or thing, and its look fits this film's "
               "style. Never take the story, places, characters or tone from the library: the brief alone decides them. "
               "When nothing fits, plan the shot without references.")
LIBRARY_NONE = ("Library: not used for this film (my choice: a fresh start). Don't use list_assets or any existing "
                "library asset, and don't borrow ideas, names, places or characters from my other projects: everything "
                "comes from the brief above.")


async def library_section(s, project: dict) -> list[str]:
    if project.get("plan_library") == "none":
        return [LIBRARY_NONE]
    return [LIBRARY_FIT, *await library_catalogue(s)]


async def storyboard_summary(s, project_id: str) -> list[str]:
    """The storyboard so far, scene by scene: enough for a part planned in a fresh conversation to carry on (what
    happens in each shot, how long, who's in it) without the earlier parts' conversation."""
    scenes = {x["id"]: x for x in await s.storyboard.scenes(project_id)}
    lines, scene_id, total = [], object(), 0.0
    for sid in await s.storyboard.order(project_id):
        sh = await s.db.fetchone("SELECT * FROM shots WHERE id = ?", (sid,))
        takes = (await s.db.fetchone("SELECT COUNT(*) AS n FROM takes WHERE shot_id = ?", (sid,)))["n"]
        scene = scenes.get(sh["scene_id"])
        if sh["scene_id"] != scene_id:
            scene_id = sh["scene_id"]
            lines.append(f"Scene “{scene['title']}” (scene_id {scene['id']}){': ' + scene['time_place'] if scene.get('time_place') else ''}"
                         if scene else "No scene:")
        refs = await s.db.fetchall("SELECT DISTINCT a.name FROM shot_refs r JOIN variants v ON v.id = r.variant_id "
                                   "JOIN assets a ON a.id = v.asset_id WHERE r.shot_id = ? ORDER BY r.position", (sid,))
        seconds = round((sh["frames"] - 1) / 24, 1)
        total += seconds
        state = ", ".join(filter(None, [sh["status"], "locked" if sh["locked"] else "", f"{takes} take(s)" if takes else ""]))
        idea = " ".join(str(sh.get("idea") or "").split())[:140]
        lines.append(f"  - shot {sh['position']} “{sh['title']}”, {seconds} s [{state}]"
                     f"{' · with ' + ', '.join('{' + r['name'] + '}' for r in refs) if refs else ''}{': ' + idea if idea else ''}")
    if lines:
        lines.append(f"Planned so far: {total:.0f} s.")
    return lines


async def music_video_rules(s, project: dict) -> list[str]:
    """A music video's song, lyrics and method, in place of the sound rule: shots cover the song back to back."""
    mode = project.get("music_mode")
    if not mode:
        return []
    song = project.get("song_file_id") and await s.db.fetchone("SELECT duration_s FROM library_files WHERE id = ?",
                                                               (project["song_file_id"],))
    length = f"{song['duration_s']:.1f} s" if song and song["duration_s"] else "not chosen yet (plan to the target length)"
    lyrics = (project.get("lyrics") or "").strip()
    rules = [
        f"This is a music video. One song plays from start to end under the whole film; its length is {length}.",
        *(["The lyrics, in order (sung exactly like this):", lyrics] if lyrics else ["The song has no lyrics."]),
        "Plan the shots back to back in film order so they cover the song exactly once: their lengths add up to the "
        "song's length, and every transition is a cut. A shot's idea quotes the lyric line(s) sung during it, in order, "
        "so each shot lasts as long as its lines; the next shot carries on where it ends.",
    ]
    if mode == "lipsync":
        rules.append(
            "Each shot is rendered from its own slice of the song, and the performer's mouth follows it. When the "
            "performer sings on screen, keep their face clearly visible and say in the prompt that they are singing "
            "with natural lip movements synchronized to the music. Never write the lyrics or any spoken words in the "
            "prompt (the model would voice its own). Sound: lines [] and no ambience; the song is the sound.")
    else:
        rules.append("Shots are picture only and the song is laid over them. Sound: lines [] and no ambience; "
                     "the song is the sound. Show singing where the lyrics are sung, but never write the words in the "
                     "prompt.")
    return rules


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
        *await library_section(s, project),
    ]
    if current:
        lines += ["Current storyboard:", *current]
        if not replace:
            lines.append("Carry on from it: plan the next one or two scenes of my brief that aren't in the storyboard "
                         "yet, in film order, with the same characters, places and style (don't repeat a scene). If "
                         "every scene of my brief is there, reply starting with “All planned:” instead.")
    lines += [
        "Plan the film in parts, in film order: each plan_storyboard call holds ONE or TWO scenes (at most 6 shots); "
        "a call with a whole long film is too much to write at once and fails. After each part is saved FilmLab asks "
        "for the next. A scene has title, goal, time_place and mood, location_asset_id (the library place it's set "
        "in, by name: {Place_Name}; FilmLab adds that place's picture to each of the scene's shots that names no "
        "other place, so give every scene set in a library place its location), and its shots. Per shot: title; idea (one plain "
        "line of what happens); prompt; seconds (2-10; the whole film near the target: budget the target across my "
        "brief's scenes before you start, and if my brief times its scenes, e.g. 0-8s, keep each scene within its "
        "time); transition into the next shot (cut "
        "unless another one helps the story); rationale (why this shot, these references and this length); sound. "
        "The prompt is the picture only: it places each character in the frame with an action and a few words on "
        "their look, and adds camera, light and mood. Every library character, place or thing in the shot is named "
        "in braces exactly as listed above, e.g. “{Hero_Name} draws his sword in {Place_Name}”: FilmLab attaches "
        "its picture and numbers it (main character first, then the setting; at most 4 per shot). Don't write "
        "refs or <Picture N> yourself.",
        SETTING_RULE.replace("this one prompt", "one shot's prompt"),
        *(await music_video_rules(s, project) or [SOUND_RULE]),
        "If plan_storyboard returns errors, nothing of that part was written: fix exactly those and call it again.",
        ("Set replace_drafts: true in the FIRST part only. Your plan replaces the unlocked draft shots without takes; "
         "locked, approved and rendered shots stay as they are (the tool lists them). To add shots to an existing "
         "scene, give its scene_id."
         if replace else
         "Don't set replace_drafts: existing shots stay, and your scenes are added after them."),
        ("Every shot is planned without references (refs: []); if a recurring character or place needs a consistent "
         "look, you may request_image a NEW design for it, made for this film's brief and style. "
         if project.get("plan_library") == "none" else
         "If something the story needs isn't in the library, plan the shot without it and request_image it. ") +
        "Don't request a render. When the whole brief is planned, reply starting with “All planned:” and a short "
        "summary: scenes, shots, total length, and anything missing.",
    ]
    return "\n".join(lines)
