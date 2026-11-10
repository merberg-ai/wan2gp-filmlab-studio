"""The director's tools. They are executed by the backend, scoped to the
project the conversation belongs to, and can never start a render or an image
generation: the strongest actions are request_render and request_image, which
only create pending approvals. The director reads the shared library and can
assign references to draft shots; it can't upload, lock or edit library items.
"""

import json
import logging

import httpx

from .. import library_context, refnames, shots
from ..capabilities import setting_error, setting_problem, picture_problem
from ..db import new_id, now
from ..edit import EditError
from ..library import MAX_REFS_PER_SHOT, LibraryError
from ..presets import PresetError
from ..renders import RenderError
from ..runs import RunError
from ..storyboard import LIBRARY_OFF, PlanInvalid, library_off_limits
from ..wangp.client import WanGPError

log = logging.getLogger("filmlab.director")


def _fn(name, description, properties=None, required=()):
    """A tool schema. Top-level required arguments are marked in their descriptions rather than in a `required`
    list: OpenClaw rejects a call that misses one before FilmLab sees it ("missing required scenes"), so a model
    that wraps its arguments ({"plan_storyboard": {"scenes": ...}}) could never recover. FilmLab unwraps such
    calls and checks required arguments itself, with errors the director can act on."""
    props = {k: ({**v, "description": "Required. " + v.get("description", "")} if k in required else v)
             for k, v in (properties or {}).items()}
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": {"type": "object", "properties": props}}}


_SOUND = {"type": "object", "description": (
    "The shot's sound. FilmLab writes it into the video model's own format, so the model speaks exactly these words. "
    "lines: [] means nobody speaks (always say so when nobody does)."), "properties": {
    "lines": {"type": "array", "maxItems": 6, "description": "Every spoken line, in order; [] = nobody speaks",
              "items": {"type": "object", "properties": {
                  "speaker": {"type": "string", "description": "The character's library name (as in the refs), or "
                                                               "e.g. 'a radio announcer' for a voice off screen"},
                  "text": {"type": "string", "description": "The exact words, nothing else (no quotes, no stage directions)"},
                  "delivery": {"type": "string", "description": "How it is said, e.g. 'whispering, amazed'"},
                  "language": {"type": "string", "description": "Omit for English"}}}},
    "ambience": {"type": "string", "description": "Sounds of the place and the action, e.g. 'servo whirr, low hum'"},
    "music": {"type": "string", "description": "Score the audience hears; omit for none"}}}

NAMES = 'Name library assets in the prompt in braces, exactly as the library lists them: {Heavy_Metal_Antihero}, or a view: {Heavy_Metal_Antihero/front portrait}. FilmLab attaches their pictures in that order and writes <Picture N>; leave refs empty then.'

_SHOT_FIELDS = {
    "title": {"type": "string", "description": "Short shot name, e.g. 'Establishing wide'"},
    "description": {"type": "string", "description": "What happens and why, for the owner"},
    "prompt": {"type": "string", "description": "The picture: subject, action, camera, light, mood. No sound or speech "
                                                "here; they go in sound"},
    "sound": _SOUND,
    "frames": {"type": "integer", "description": "Clip length in frames at 24 fps; snapped to 8n+1 (121 = 5 s, max 481); "
                                                "omit to use the project's default length"},
    "resolution": {"type": "string", "description": "WIDTHxHEIGHT; omit to use the project's default size"},
    "template": {"type": "string", "description": "WanGP video template name; omit to follow FilmLab's defaults "
                                                  "(they switch automatically between shots with and without references)"},
    "seed": {"type": "integer", "description": "-1 for random"},
    "transition": {"type": "string", "enum": list(shots.TRANSITIONS), "description": "Into the next shot"},
}

_REF = {"type": "object", "properties": {
    "file_id": {"type": "string", "description": "An image id (images[].file_id from get_asset); enough on its own"},
    "variant_id": {"type": "string", "description": "A look id (variant_id from get_asset); alone, uses its first image"},
    "role": {"type": "string", "description": "subject, setting, prop or style"}}}

_PLAN_SHOT = {"type": "object", "required": ["title", "prompt", "seconds", "rationale"], "properties": {
    "title": {"type": "string"},
    "idea": {"type": "string", "description": "One plain line of what happens"},
    "prompt": {"type": "string", "description": "The picture; no sound or speech (they go in sound). " + NAMES},
    "sound": _SOUND,
    "seconds": {"type": "number", "description": "2-10 typically; snapped to the model's frame grid"},
    "resolution": {"type": "string", "description": "Omit for the project's default"},
    "refs": {"type": "array", "maxItems": 8, "items": {"type": "string"},
             "description": "Optional: library image file_ids in <Picture N> order. Not needed when the prompt names "
                            "assets in {braces}"},
    "transition": {"type": "string", "enum": list(shots.TRANSITIONS), "description": "Into the next shot"},
    "rationale": {"type": "string", "description": "Why this shot, these references and this length"}}}
_PLAN_SCENE = {"type": "object", "required": ["shots"], "properties": {
    "scene_id": {"type": "string", "description": "Add to an existing scene instead of creating one"},
    "title": {"type": "string"}, "goal": {"type": "string", "description": "What the scene is for in the story"},
    "time_place": {"type": "string"}, "mood": {"type": "string"},
    "location_asset_id": {"type": "string", "description": "The library location this scene is set in, by name: "
                                                          "{Place_Name}. Each shot that names no place gets its picture "
                                                          "as the setting"},
    "shots": {"type": "array", "items": _PLAN_SHOT}}}

TOOLS = [
    _fn("get_project", "Get this project: brief, style (follow it in every prompt), target length, scenes, and every "
                       "shot in storyboard order with its "
                       "status, lock, transition, references and the GPU estimate."),
    _fn("plan_storyboard", "Write a whole storyboard plan in one call: scenes and their shots, in film order. FilmLab "
                           "checks everything first; if anything is wrong NOTHING is written and you get every error to "
                           "fix. Shots are created as drafts. replace_drafts: true replaces unlocked draft shots without "
                           "takes (locked, approved and rendered shots are always kept).",
        {"logline": {"type": "string"}, "replace_drafts": {"type": "boolean"},
         "scenes": {"type": "array", "items": _PLAN_SCENE, "maxItems": 12}}, ("scenes",)),
    _fn("update_scene", "Change a scene's title, goal, time and place, mood or location.",
        {"scene_id": {"type": "string"}, "title": {"type": "string"}, "goal": {"type": "string"},
         "time_place": {"type": "string"}, "mood": {"type": "string"}, "location_asset_id": {"type": "string"}},
        ("scene_id",)),
    _fn("move_shot", "Move a shot to another scene and/or place (index within that scene, 0 = first). "
                     "Omit scene_id to move it to Unsorted.",
        {"shot_id": {"type": "string"}, "scene_id": {"type": "string"}, "index": {"type": "integer", "minimum": 0}},
        ("shot_id",)),
    _fn("request_storyboard_render", "Ask the owner to approve rendering these shots as one batch, in storyboard "
                                     "order. Does NOT render; the owner reviews and starts it in the cockpit.",
        {"shot_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
         "note": {"type": "string", "description": "Why these shots are ready"}}, ("shot_ids",)),
    _fn("list_video_templates", "Installed WanGP video templates: reference support (none/single/multiple), whether "
                                "each is usable, and FilmLab's configured defaults."),
    _fn("list_assets", "Search the shared reference library (characters, locations, props, styles). With shot_id (or "
                       "scene_id) the results are ranked for that shot: named in it, the scene's location, used by other "
                       "shots of the scene, in the cast, matching words. Each result says why and which look to use "
                       "(look.file_id keeps continuity with the scene); pass that file_id to assign_refs.",
        {"query": {"type": "string"}, "kind": {"type": "string", "enum": ["character", "location", "prop", "style", "sound"]},
         "shot_id": {"type": "string"}, "scene_id": {"type": "string"}}),
    _fn("list_presets", "Model presets: a template with its size, length, what references it takes and how to write "
                        "the prompt for it. Only presets with status 'ready' can be used (the others need model files "
                        "the owner hasn't downloaded in WanGP)."),
    _fn("apply_preset", "Set a shot's template, size and length from a preset (keep_length: true keeps its length). "
                        "Not on locked, approved or rendering shots. Say why in rationale.",
        {"shot_id": {"type": "string"}, "preset_id": {"type": "string"}, "keep_length": {"type": "boolean"},
         "rationale": {"type": "string"}}, ("shot_id", "preset_id", "rationale")),
    _fn("get_asset", "One library asset with its variants (looks), versions, lock state and images.",
        {"asset_id": {"type": "string"}}, ("asset_id",)),
    _fn("assign_refs", "Set a shot's ordered reference images (image file_ids from get_asset). Position N is "
                       "<Picture N> in the prompt: mention the pictures in the prompt by that name. Replaces the "
                       "shot's current references. Call it before update_shot. Not on locked shots.",
        {"shot_id": {"type": "string"}, "refs": {"type": "array", "items": _REF, "maxItems": 8},
         "rationale": {"type": "string", "description": "Why these references"}}, ("shot_id", "refs")),
    _fn("request_image", "Ask the owner to generate reference images for a variant with a WanGP image model. Does NOT "
                         "generate; the owner approves in the cockpit Library.",
        {"variant_id": {"type": "string"}, "prompt": {"type": "string", "description": "Image prompt"},
         "input_file_ids": {"type": "array", "items": {"type": "string"},
                            "description": "Library images to use as references (keeps identity); omit for text only"},
         "count": {"type": "integer", "minimum": 1, "maximum": 4},
         "note": {"type": "string", "description": "Why this image is needed"}}, ("variant_id", "prompt")),
    _fn("draft_shot", "Create one new draft shot (for a whole storyboard use plan_storyboard).",
        {**_SHOT_FIELDS, "scene_id": {"type": "string"}, "rationale": {"type": "string"}}, ("title", "prompt")),
    _fn("update_shot", "Edit a shot that isn't approved, rendering or locked. Editing withdraws a pending approval; on a "
                       "rendered shot the change applies to its next take. Always say why in rationale: the owner "
                       "sees it in the shot's history.",
        {"shot_id": {"type": "string"}, **_SHOT_FIELDS, "rationale": {"type": "string"}}, ("shot_id", "rationale")),
    _fn("validate_shot", "Preflight a shot with WanGP without rendering: final size, duration, model availability.",
        {"shot_id": {"type": "string"}}, ("shot_id",)),
    _fn("request_render", "Ask the owner to approve rendering a shot. Does NOT render; the owner decides in the cockpit.",
        {"shot_id": {"type": "string"}, "note": {"type": "string", "description": "Why this shot is ready"}}, ("shot_id",)),
    _fn("get_render_status", "Latest render job and takes for a shot.", {"shot_id": {"type": "string"}}, ("shot_id",)),
    _fn("review_takes", "Every take of every shot with the owner's verdict (good/maybe/reject), notes, and which take "
                        "the edit uses."),
    _fn("get_edit", "The edit (the film being assembled): clips in order with trims, transitions and lengths, music, "
                    "titles, captions and the total length, plus any problems."),
    _fn("suggest_edit", "Propose an edit change for the owner to accept or reject in the cockpit (nothing changes until "
                        "they accept). change.clips: {shot_id: {in, out (frames), transition_s, gain_db, mute, skip}}; "
                        "change.texts_add: [{kind: title, text, at: start|end|shot_id, duration_s} or "
                        "{kind: caption, text, start_s, end_s}]. Explain why in note.",
        {"change": {"type": "object"}, "note": {"type": "string"}}, ("change", "note")),
    _fn("inspect_take", "FilmLab's inspection of a take: technical checks (length, black or frozen picture, silent or "
                        "clipping sound) and a vision check of four keyframes against the prompt and scene (setting, "
                        "which characters are visible, defects, prompt match 1-5). Runs it if it hasn't run yet.",
        {"take_id": {"type": "string"}}, ("take_id",)),
    _fn("propose_asset", "Draft a NEW library asset (character, location, prop, style, or sound = music) the owner "
                         "asked for or the film needs and the library lacks. Nothing is generated and nothing enters "
                         "the library: the owner reviews the draft in the Library's Creator, generates its images (and "
                         "voice or music) and approves it. views: the first is the anchor (a complete text-to-image "
                         "prompt); later views are edits of the kept anchor (say what changes and that the rest stays "
                         "identical). Characters may get a voice {description, line}; a sound needs music {style, lyrics, "
                         "seconds}.",
        {"kind": {"type": "string", "enum": ["character", "location", "prop", "style", "sound"]},
         "name": {"type": "string"}, "description": {"type": "string", "description": "What must stay the same in every shot"},
         "tags": {"type": "string"},
         "views": {"type": "array", "maxItems": 4, "items": {"type": "object", "properties": {
             "label": {"type": "string"}, "prompt": {"type": "string"}}}},
         "voice": {"type": "object", "properties": {"description": {"type": "string"}, "line": {"type": "string"}}},
         "music": {"type": "object", "properties": {"style": {"type": "string"}, "lyrics": {"type": "string"},
                                                    "seconds": {"type": "integer"}}},
         "why": {"type": "string", "description": "Why the film needs it (the owner sees this)"}},
        ("kind", "name", "description")),
    _fn("propose_redo", "Propose a new take of a shot: a revised prompt and/or seed (-1 = new random seed) and the "
                        "reason. Does NOT render by itself: outside a redo run the owner accepts it in the cockpit; in a "
                        "redo run the owner started, FilmLab renders it within the owner's limits. Not for locked shots.",
        {"shot_id": {"type": "string"}, "reason": {"type": "string", "description": "What's wrong and what the change fixes"},
         "prompt": {"type": "string", "description": "The full revised prompt; omit to keep the current one"},
         "seed": {"type": "integer"}}, ("shot_id", "reason")),
    _fn("propose_transitions", "Propose the transition (and its length) for cuts of the edit, when the owner asks you to "
                               "plan transitions. Only proposes: the owner reviews the list and applies it. One entry "
                               "per cut; shot_id is the shot the transition leaves (get_edit lists the clips in order).",
        {"transitions": {"type": "array", "items": {"type": "object", "properties": {
            "shot_id": {"type": "string"},
            "transition": {"type": "string", "enum": list(shots.TRANSITIONS)},
            "seconds": {"type": "number", "description": "Length in seconds; 0 for a cut; at most the cut's max_s"},
            "reason": {"type": "string", "description": "One short line for the owner"}}}},
         "summary": {"type": "string", "description": "Your approach in a sentence or two"}}, ("transitions",)),
    _fn("propose_story", "Propose the Storybook's text when the owner asks you to write the story: one entry per shot "
                         "(a page's narration, or a comic panel's caption; empty for none). Only proposes: the owner "
                         "edits and accepts it.",
        {"pages": {"type": "array", "items": {"type": "object", "properties": {
            "shot_id": {"type": "string"}, "text": {"type": "string"}}}},
         "title": {"type": "string", "description": "A title for the cover, if better than the current one"},
         "subtitle": {"type": "string", "description": "A short subtitle or tagline for the cover"},
         "summary": {"type": "string", "description": "How you told it, in a sentence"}}, ("pages",)),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
ARG_NAMES = {t["function"]["name"]: set(t["function"]["parameters"]["properties"]) for t in TOOLS}
WRAPPERS = ("update", "updates", "fields", "changes", "shot", "args", "arguments", "data")


def _check_args(name: str, args: dict) -> dict:
    """Unpack a wrapper object models sometimes add ({"update": {...}}); refuse unknown argument names,
    so a misnamed field is an error the director sees, not a silent no-op."""
    allowed = ARG_NAMES[name]
    for w in (name, *WRAPPERS):  # also the tool's own name: {"plan_storyboard": {"scenes": [...]}}
        if w not in allowed and isinstance(args.get(w), dict):
            inner = args.pop(w)
            args = {**inner, **args}
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ValueError(f"unknown argument(s) {', '.join(unknown)} for {name}; it takes: {', '.join(sorted(allowed))}")
    return args


SCENE_KEYS = ("title", "goal", "time_place", "mood", "location_asset_id")


def _fold_plan(args: dict) -> tuple[dict, list[str]]:
    """Repair plan_storyboard slips models make: a scene's fields written beside "scenes" instead of inside a
    scene, and a scene without a title. Returns the repaired arguments and what was changed (shown to the model)."""
    fixes: list[str] = []
    scenes = args.get("scenes")
    if not isinstance(scenes, list) or not scenes or not all(isinstance(x, dict) for x in scenes):
        return args, fixes
    stray = {k: args.pop(k) for k in SCENE_KEYS if k in args}
    for k, v in stray.items():
        i = 0 if len(scenes) == 1 else next((n for n, sc in enumerate(scenes) if k not in sc), None)
        if i is None:
            fixes.append(f"{k} was outside the scenes and every scene has its own, so it was ignored")
        elif k in scenes[i]:
            fixes.append(f"{k} was outside the scenes; scenes[{i}] keeps its own")
        else:
            scenes[i][k] = v
            fixes.append(f"{k} was outside the scenes, so it went into scenes[{i}]")
    for n, sc in enumerate(scenes):
        if not sc.get("scene_id") and not str(sc.get("title") or "").strip():
            sc["title"] = f"Scene {n + 1}"
            fixes.append(f"scenes[{n}] had no title, so it is called \"Scene {n + 1}\" (rename it with update_scene)")
        for m, sh in enumerate(sc.get("shots") or []):
            if isinstance(sh, dict):
                fixes += _fold_sound(sh, f"scenes[{n}].shots[{m}]")
    return args, fixes


def _fold_sound(shot: dict, where: str) -> list[str]:
    """A shot's sound written as plain text (it's the ambience), or a line written as "Speaker: words"."""
    s = shot.get("sound")
    if isinstance(s, str):
        shot["sound"] = {"lines": [], "ambience": s.strip(), "music": ""}
        return [f"{where}.sound was text, so it became the ambience (no spoken lines)"]
    if not isinstance(s, dict) or not isinstance(s.get("lines"), list):
        return []
    fixes, lines = [], []
    for i, ln in enumerate(s["lines"]):
        if isinstance(ln, str):
            speaker, sep, text = ln.partition(":")
            if sep and speaker.strip() and text.strip() and len(speaker) <= 60:
                lines.append({"speaker": speaker.strip(), "text": text.strip().strip('"“”'), "delivery": ""})
                fixes.append(f"{where}.sound.lines[{i}] was text, so it became {{speaker, text}}")
                continue
        lines.append(ln)
    s["lines"] = lines
    return fixes


def _normalise_refs(refs) -> list[dict]:
    """Models sometimes send refs as a JSON string or as bare ids; accept those, reject the rest.
    A bare id may be a variant or an image; _resolve_ref tells them apart."""
    if isinstance(refs, str):
        try:
            refs = json.loads(refs)
        except ValueError:
            raise ValueError("refs must be a list of {variant_id, file_id?, role?} objects") from None
    if not isinstance(refs, list):
        raise ValueError("refs must be a list of {variant_id, file_id?, role?} objects")
    out = []
    for r in refs:
        if isinstance(r, str):
            r = {"variant_id": r}
        if not isinstance(r, dict) or not (r.get("variant_id") or r.get("file_id")):
            raise ValueError("each ref needs a file_id or variant_id (from get_asset)")
        out.append(r)
    return out


def _shot_view(s: dict) -> dict:
    return {k: s[k] for k in ("id", "position", "title", "idea", "prompt", "template", "resolution", "frames", "seed",
                              "status", "created_by", "scene_id", "transition", "rationale")} | {
        "seconds": round((s["frames"] - 1) / 24, 2), "locked": bool(s["locked"]),
        "sound": json.loads(s["sound"]) if s.get("sound") else None}


class DirectorTools:
    ACTOR = "director"

    def __init__(self, studio):
        self.studio = studio
        self.db, self.bus, self.renders = studio.db, studio.bus, studio.renders
        self.library, self.caps = studio.library, studio.caps
        self.plan_parts: dict[str, int] = {}  # project -> plan parts saved in the current chat turn

    async def run(self, project_id: str, name: str, raw_args: str) -> dict:
        if name not in TOOL_NAMES:
            return {"error": f"unknown tool {name}"}
        fixes: list[str] = []  # what FilmLab repaired in the arguments; the director sees it either way
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
            if name == "plan_storyboard":
                args, fixes = _fold_plan(args)
            out = await getattr(self, name)(project_id, **_check_args(name, args))
            return out | {"fixed_by_filmlab": fixes} if fixes and isinstance(out, dict) else out
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}
        except PlanInvalid as exc:
            return {"error": "the plan wasn't written; fix these and call plan_storyboard again", "errors": exc.errors} | (
                {"fixed_by_filmlab": fixes} if fixes else {})
        except (ValueError, shots.ShotError, RenderError, LibraryError, EditError, RunError, PresetError) as exc:
            return {"error": str(exc)}
        except httpx.HTTPError as exc:
            return {"error": f"WanGP is not reachable right now ({type(exc).__name__}); tell the owner"}
        except WanGPError as exc:
            return {"error": f"WanGP returned an error: {exc}; tell the owner"}
        except Exception as exc:  # a tool bug must not end the director's turn; let it see the error
            log.exception("director tool %s failed", name)
            return {"error": f"internal error in {name} ({type(exc).__name__}); check the arguments or tell the owner"}

    async def get_project(self, project_id):
        project = await self.db.fetchone("SELECT id, title, logline, brief, style, plan_library, target_seconds, default_resolution, "
                                         "default_frames FROM projects WHERE id = ?", (project_id,))
        rows = await self.db.fetchall("SELECT * FROM shots WHERE project_id = ? AND deleted_at IS NULL ORDER BY position",
                                      (project_id,))
        scenes = [{k: x[k] for k in ("id", "position", "title", "goal", "time_place", "mood", "location_asset_id")}
                  for x in await self.studio.storyboard.scenes(project_id)]
        out = []
        for s in rows:
            refs = await self.library.shot_refs(s["id"])
            out.append(_shot_view(s) | {"follows_defaults": not s["template_explicit"], "references": [
                {"picture": r["position"], "asset": r["asset_name"], "look": r["variant_name"], "version": r["version"],
                 "variant_id": r["variant_id"], "role": r["role"]} for r in refs]})
        cast = [{"asset_id": a["id"], "kind": a["kind"], "name": a["name"]} for a in await self.library.cast(project_id)]
        estimate = await self.studio.storyboard.estimate(rows)
        return project | {"cast": cast, "scenes": scenes, "shots": out, "total_seconds": round(
            sum((r["frames"] - 1) / 24 for r in rows), 1), "gpu_estimate": estimate}

    async def list_video_templates(self, project_id):
        matrix = await self.caps.matrix()
        templates = [{k: t.get(k) for k in ("template", "task", "refs", "requires_refs", "audio", "valid", "installed",
                                            "reason", "specialities")}
                     for t in matrix if t["task"] in ("gen_video", "gen_video_with_refs")]
        project = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        cfg = await self.caps.settings()
        return {"templates": templates, "defaults": {
            "with_references": project["video_refs_template"] or cfg["video_refs"],
            "without_references": project["video_plain_template"] or cfg["video_plain"]}}

    async def list_assets(self, project_id, query="", kind=None, shot_id=None, scene_id=None):
        if shot_id or scene_id:
            res = await library_context.suggest(self.db, self.library, project_id, shot_id=shot_id, scene_id=scene_id,
                                                query=query, kind=kind, limit=30)
            return {"ranked_for": res["context"], "assets": [
                {k: a[k] for k in ("id", "kind", "name", "description", "tags", "reasons", "on_shot", "look")}
                for a in res["assets"]]}
        rows = await self.library.search(query, kind, limit=50)
        return {"assets": [{k: a[k] for k in ("id", "kind", "name", "description", "tags", "variant_count")} for a in rows]}

    async def list_presets(self, project_id):
        return {"presets": [{k: p[k] for k in ("id", "name", "template", "resolution", "seconds", "frames", "refs", "use",
                                               "tips", "status", "reason", "audio", "source")}
                            for p in await self.studio.presets.listing()]}

    async def apply_preset(self, project_id, shot_id, preset_id, rationale="", keep_length=False):
        if not str(rationale or "").strip():
            raise ValueError("rationale is required: say briefly why this preset suits the shot")
        shot = await shots.get_shot(self.db, shot_id, project_id)
        updated = await self.studio.presets.apply(shot, str(preset_id), self.ACTOR, bool(keep_length), rationale)
        return {"shot": _shot_view(updated)}

    async def get_asset(self, project_id, asset_id):
        a = await self.library.asset(asset_id)
        return {k: a[k] for k in ("id", "kind", "name", "description", "notes", "tags")} | {
            **({"has_voice": bool(a.get("voice"))} if a["kind"] == "character" else {}), "variants": [
            {"variant_id": v["id"], "name": v["name"], "version": v["version"], "locked": v["locked"],
             "description": v["description"],
             "images": [{"file_id": f["file_id"], "caption": f["caption"], "source": f["source"]}
                        for f in v["files"] if f["kind"] == "image"]} for v in a["variants"]]}

    async def assign_refs(self, project_id, shot_id, refs, rationale=""):
        shot = await shots.get_shot(self.db, shot_id, project_id)
        if shot["status"] not in shots.EDITABLE:
            raise shots.ShotError(f"shot is {shot['status']}; the director can't change a shot while it is approved or rendering")
        if shot["locked"]:
            raise shots.ShotError(f"shot {shot['position']} is locked by the owner; propose the change in chat instead")
        refs = [await self._resolve_ref(r) for r in _normalise_refs(refs)]
        project = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        for r in refs:
            v = await self.db.fetchone("SELECT asset_id FROM variants WHERE id = ?", (r["variant_id"],))
            if v and await library_off_limits(self.db, project, v["asset_id"]):
                raise ValueError(LIBRARY_OFF)
        result = await self.library.set_shot_refs(shot, refs, self.ACTOR, rationale)
        await self.studio.refs.sync_template_soon(shot, len(result))
        await shots.refs_changed(self.db, self.bus, shot, self.ACTOR)
        legend = [f"<Picture {r['position']}> = {r['asset_name']} · {r['variant_name']} v{r['version']}" for r in result]
        return {"references": legend, "message": "Use these picture names in the shot prompt."}

    async def _resolve_ref(self, ref: dict) -> dict:
        """An image id alone (or given as variant_id) is resolved to the newest look that holds it."""
        ref = dict(ref)
        vid = str(ref.get("variant_id") or "")
        if vid and await self.db.fetchone("SELECT 1 FROM variants WHERE id = ? AND deleted_at IS NULL", (vid,)):
            return ref
        fid = str(ref.get("file_id") or vid)
        row = await self.db.fetchone(
            "SELECT v.id FROM variant_files vf JOIN variants v ON v.id = vf.variant_id JOIN assets a ON a.id = v.asset_id "
            "WHERE vf.file_id = ? AND v.deleted_at IS NULL AND a.deleted_at IS NULL "
            "ORDER BY v.version DESC, v.created_at DESC LIMIT 1", (fid,))
        if not row:
            raise ValueError(f"'{fid}' is neither a variant_id nor an image file_id; get_asset lists both")
        return ref | {"variant_id": row["id"], "file_id": fid}

    async def propose_asset(self, project_id, kind, why="", **plan):
        from ..creator import CreatorError
        try:
            c = await self.studio.creator.from_director(kind, plan, why or plan.get("description", ""))
        except CreatorError as exc:
            raise ValueError(f"not drafted: {exc}") from None
        return {"creation_id": c["id"], "status": "waiting for the owner",
                "message": "Drafted. The owner reviews it in the Library (Creator), generates its images and approves "
                           "it; it isn't in the library or usable in shots until then."}

    async def request_image(self, project_id, variant_id, prompt, input_file_ids=None, count=None, note=""):
        v = await self.library.variant(variant_id)
        prompt = str(prompt or "").strip()
        if not 3 <= len(prompt) <= 4000:
            raise ValueError("prompt must be 3-4000 characters")
        files = [str(f) for f in (input_file_ids or [])][:8]
        for fid in files:
            await self.library.file(fid)
        rid = new_id()
        await self.db.execute(
            "INSERT INTO image_requests (id, variant_id, requested_by, prompt, input_file_ids, count, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, variant_id, self.ACTOR, prompt, json.dumps(files), max(1, min(4, int(count or 2))), str(note)[:500], now()))
        await self.db.audit(self.ACTOR, "image_request.create", variant_id, {"request_id": rid})
        await self.bus.publish("library.updated", {"asset_id": v["asset_id"], "image_request": rid})
        return {"request_id": rid, "status": "pending",
                "message": "Waiting for the owner to approve in the FilmLab Library. Nothing is generating yet."}

    async def _says_its_setting(self, scene_id: str | None, prompt) -> None:
        """The director's prompts must state their scene's place and time of day (the video model sees nothing else)."""
        if not scene_id or not prompt:
            return
        row = await self.db.fetchone("SELECT time_place FROM scenes WHERE id = ? AND deleted_at IS NULL", (scene_id,))
        problem = setting_problem(str(prompt), (row and row["time_place"]) or "")
        if problem:
            raise ValueError(setting_error(problem) + "; nothing was saved, call it again with the fixed prompt")

    async def _names_its_pictures(self, shot_id: str, prompt) -> None:
        """The director's prompts must name exactly the shot's attached pictures, from <Picture 1>."""
        if not prompt:
            return
        refs = await self.db.fetchall(
            "SELECT r.position, a.name FROM shot_refs r JOIN variants v ON v.id = r.variant_id JOIN assets a ON "
            "a.id = v.asset_id WHERE r.shot_id = ? ORDER BY r.position", (shot_id,))
        problem = picture_problem(str(prompt), len(refs))
        if problem:
            legend = "; ".join(f"<Picture {i}> = {r['name']}" for i, r in enumerate(refs, start=1)) or "none"
            raise ValueError(f"{problem}. The shot's references now: {legend}. To drop a character or place from the "
                             "shot, remove its reference with assign_refs first (the prompt alone doesn't remove "
                             "it). Nothing was saved; call it again with the fixed prompt")

    async def _named(self, prompt) -> dict | None:
        """{Asset names} in a prompt (refnames.py): the prompt with <Picture N>, and the refs to attach."""
        named = await refnames.resolve_prompt(self.db, str(prompt or ""), MAX_REFS_PER_SHOT)
        if named and named["errors"]:
            raise ValueError("; ".join(named["errors"]) + ". Nothing was saved")
        return named

    async def _attach_named(self, project_id, shot_id, named, rationale) -> list[str]:
        res = await self.assign_refs(project_id, shot_id, [{"variant_id": r["variant_id"], "file_id": r["file_id"]}
                                                           for r in named["refs"]],
                                     rationale or "references named in the prompt")
        return res["references"] + named["notes"]

    async def draft_shot(self, project_id, template=None, scene_id=None, rationale="", **fields):
        fields.pop("idea", None)  # the idea is the owner's own words
        named = await self._named(fields.get("prompt")) if fields.get("prompt") else None
        if named:
            fields["prompt"] = named["prompt"]
        if scene_id:
            await self.studio.storyboard.scene(scene_id, project_id)
            await self._says_its_setting(scene_id, fields.get("prompt"))
        tpl, explicit = await self.studio.new_shot_template(project_id, template)
        shot = await shots.create_shot(self.db, self.bus, project_id, fields, tpl, self.ACTOR, explicit,
                                       scene_id=scene_id, rationale=rationale)
        await self.studio.storyboard.renumber(project_id)
        out = {"references": await self._attach_named(project_id, shot["id"], named, rationale)} if named else {}
        return {"shot": _shot_view(await shots.get_shot(self.db, shot["id"]))} | out

    async def update_shot(self, project_id, shot_id, template=None, rationale="", **fields):
        fields.pop("idea", None)  # the idea is the owner's own words
        if not str(rationale or "").strip():
            raise ValueError("rationale is required: say briefly why you're changing this shot (the owner sees it)")
        shot = await shots.get_shot(self.db, shot_id, project_id)
        if not fields and not template:
            raise ValueError("nothing to change: pass the fields to update (e.g. prompt) as top-level arguments")
        tpl = await self.renders.resolve_template(template) if template else None
        attached = None
        before = await shots.snapshot(self.db, shot_id)
        if "prompt" in fields:
            await self._says_its_setting(shot["scene_id"], fields["prompt"])
            named = await self._named(fields["prompt"])
            if named:  # the named assets become the shot's references, in order
                fields["prompt"] = named["prompt"]
                attached = await self._attach_named(project_id, shot_id, named, rationale)
                shot = await shots.get_shot(self.db, shot_id, project_id)
            await self._names_its_pictures(shot["id"], fields["prompt"])
        updated = await shots.update_shot(self.db, self.bus, shot, fields, self.ACTOR, tpl, rationale=rationale)
        extra = {"references": attached} if attached else {}
        if await shots.snapshot(self.db, shot_id) == before:
            return {"shot": _shot_view(updated), "note": "nothing changed: the values were already the same"} | extra
        return {"shot": _shot_view(updated)} | extra

    async def plan_storyboard(self, project_id, scenes, logline="", replace_drafts=False):
        p = await self.db.fetchone("SELECT music_mode FROM projects WHERE id = ?", (project_id,))
        if p and p["music_mode"] and isinstance(scenes, list):
            # a music video: the song is the sound of every shot, so FilmLab sets it (a slip here can't fail a plan)
            for sc in scenes:
                for sh in (sc.get("shots") or []) if isinstance(sc, dict) else []:
                    if isinstance(sh, dict):
                        sh["sound"] = {"lines": [], "ambience": "", "music": ""}
        part = self.plan_parts.get(project_id, 0)
        if part:  # a later part of a plan in parts must never replace the drafts the earlier parts just wrote
            replace_drafts = False
        result = await self.studio.storyboard.apply_plan(
            project_id, {"scenes": scenes, "logline": logline, "replace_drafts": replace_drafts}, self.ACTOR)
        if project_id in self.plan_parts:  # counted only inside a chat turn (the chat resets and clears it)
            self.plan_parts[project_id] = part + 1
        return result | {"message": "Written as draft shots. Nothing renders until the owner starts it."}

    async def update_scene(self, project_id, scene_id, **fields):
        return {"scene": await self.studio.storyboard.update_scene(scene_id, fields, self.ACTOR, project_id)}

    async def move_shot(self, project_id, shot_id, scene_id=None, index=None):
        return {"shot": _shot_view(await self.studio.storyboard.move_shot(shot_id, scene_id, index, self.ACTOR,
                                                                           project_id))}

    async def request_storyboard_render(self, project_id, shot_ids, note=""):
        order = await self.studio.storyboard.order(project_id)
        ids = [str(x) for x in shot_ids] if isinstance(shot_ids, list) else []
        if not ids or any(x not in order for x in ids):
            raise ValueError("shot_ids must be shots of this project (see get_project)")
        rid = new_id()
        await self.db.execute("INSERT INTO batch_requests (id, project_id, shot_ids, note, created_at) VALUES (?, ?, ?, ?, ?)",
                              (rid, project_id, json.dumps([x for x in order if x in ids]), str(note)[:500], now()))
        await self.db.audit(self.ACTOR, "batch_request.create", project_id, {"request_id": rid, "shots": len(ids)})
        await self.bus.publish("batch.updated", {"request_id": rid}, project_id)
        return {"request_id": rid, "status": "pending",
                "message": "Waiting for the owner to review and start the batch in the cockpit. Nothing is rendering."}

    async def validate_shot(self, project_id, shot_id):
        return await self.renders.validate(await shots.get_shot(self.db, shot_id, project_id))

    async def request_render(self, project_id, shot_id, note=""):
        shot = await shots.get_shot(self.db, shot_id, project_id)
        res = await shots.request_approval(self.db, self.bus, shot, self.ACTOR, note)
        return res | {"message": "Waiting for the owner to approve in the FilmLab cockpit. Nothing is rendering yet."}

    async def review_takes(self, project_id):
        out = []
        for sid in await self.studio.storyboard.order(project_id):
            shot = await shots.get_shot(self.db, sid)
            chosen = await self.studio.edit.chosen_take(shot)
            takes = await self.db.fetchall(
                "SELECT t.id, t.verdict, t.notes, t.created_at, i.verdict AS inspection, i.summary AS inspection_summary "
                "FROM takes t LEFT JOIN take_inspections i ON i.take_id = t.id WHERE t.shot_id = ? AND t.deleted_at IS NULL ORDER BY t.rowid",
                (sid,))
            out.append({"shot_id": sid, "position": shot["position"], "title": shot["title"],
                        "used_take": chosen["id"] if chosen else None, "takes": takes})
        return {"shots": out}

    async def get_edit(self, project_id):
        tl = await self.studio.edit.timeline(project_id)
        clips = [{k: s.get(k) for k in ("kind", "shot_id", "position", "title", "take_id", "in", "out", "frames",
                                        "start_s", "transition", "transition_s", "gain_db", "mute", "text")}
                 for s in tl["segments"]]
        return {"fps": tl["fps"], "size": f"{tl['width']}x{tl['height']}", "total_s": tl["total_s"], "segments": clips,
                "music": tl["doc"]["music"], "captions": [t for t in tl["doc"]["texts"] if t["kind"] == "caption"],
                "left_out": [sid for sid, c in tl["doc"]["clips"].items() if c.get("skip")],
                "note": "Shots in left_out were taken out of the film by the owner (Leave out). Only the owner can put "
                        "them back, with Put back in the Edit view; say so if the owner asks why a shot is missing.",
                "issues": tl["issues"]}

    async def suggest_edit(self, project_id, change, note):
        res = await self.studio.edit.suggest(project_id, change, note)
        return res | {"message": "Waiting for the owner to accept it in the Edit view. Nothing changed yet."}

    async def inspect_take(self, project_id, take_id):
        t = await self.db.fetchone("SELECT t.id FROM takes t JOIN shots s ON s.id = t.shot_id WHERE t.id = ? AND "
                                   "s.project_id = ?", (str(take_id), project_id))
        if not t:
            raise ValueError("take_id isn't a take of this project (review_takes lists them)")
        insp = await self.studio.inspector.wait(t["id"], timeout=150)
        if not insp or insp["status"] == "running":
            return {"status": "running", "message": "Still inspecting; ask again in a minute."}
        out = {k: insp[k] for k in ("status", "verdict", "summary", "technical", "vision", "model")}
        return out | ({"error": insp["error"]} if insp["error"] else {})  # "error" means the call failed

    async def propose_redo(self, project_id, shot_id, reason, prompt=None, seed=None):
        if prompt:
            await self._says_its_setting((await shots.get_shot(self.db, str(shot_id), project_id))["scene_id"], prompt)
            await self._names_its_pictures(str(shot_id), prompt)
        res = await self.studio.runs.propose(project_id, str(shot_id), reason, prompt, seed, self.ACTOR)
        where = ("FilmLab renders it in the owner's redo run, within its limits, and inspects the new take."
                 if res["run_id"] else "Waiting for the owner to accept it in the cockpit. Nothing is rendering.")
        return res | {"message": where}

    async def propose_story(self, project_id, pages, title="", subtitle="", summary=""):
        return await self.studio.story_writer.propose(project_id, pages, title, subtitle, summary)

    async def propose_transitions(self, project_id, transitions, summary=""):
        return await self.studio.transitions.propose(project_id, transitions, summary)

    async def get_render_status(self, project_id, shot_id):
        shot = await shots.get_shot(self.db, shot_id, project_id)
        job = await self.db.fetchone(
            "SELECT status, phase, current_step, total_steps, error, submitted_at, finished_at FROM render_jobs "
            "WHERE shot_id = ? ORDER BY submitted_at DESC LIMIT 1", (shot_id,))
        takes = await self.db.fetchall(
            "SELECT t.id, m.width, m.height, m.duration_s, m.has_audio, t.created_at FROM takes t "
            "JOIN media m ON m.id = t.media_id WHERE t.shot_id = ? AND t.deleted_at IS NULL ORDER BY t.rowid", (shot_id,))
        return {"shot_status": shot["status"], "latest_job": job, "takes": takes}
