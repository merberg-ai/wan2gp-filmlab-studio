"""The Creator: a library asset made from the owner's description.

1. The owner describes a character, location, prop, style or sound (or asks the director in chat).
2. The director drafts it with one propose_asset call: name, a description of what must stay consistent, tags,
   the image views to generate (the first is the anchor, made from text; the others are made from the kept
   anchor with an edit model, so it stays the same subject), a character's voice, a sound's music.
3. The owner generates each part (GPU use is always the owner's click) and keeps or discards the candidates.
4. Approve: the asset leaves draft and appears in the library. Until then it is hidden from the library, the
   director and every shot. Discard moves it to Recently deleted.
"""

import asyncio
import json
import logging

from .db import Database, new_id, now
from .director.gateway import GatewayError
from .events import EventBus
from .library import KINDS, LibraryError

log = logging.getLogger("filmlab.creator")
ACTIVE_STATES = ("drafting", "ready")
MAX_VIEWS = 4
EMPTY_REPLY = "'message': 'internal error', 'type': 'api_error'"

VIEW_GUIDE = {
    "character": "a front portrait (head and shoulders, neutral expression), a full-body view in a neutral standing "
                 "pose, and a three-quarter view; plain light-grey studio background, soft even light, so the model "
                 "can reuse the character anywhere",
    "location": "a wide establishing view, a reverse angle, and a closer detail of what makes the place recognisable; "
                "no people, at the time of day and weather it is usually seen in",
    "prop": "a clean product shot on a plain background, and the object in use or in context",
    "style": "two or three sample frames in the style with different subjects, so it reads as a look, not a scene",
    "sound": "",
}


class CreatorError(ValueError):
    pass


def _text(v, limit: int, name: str, errors: list, required=False) -> str:
    s = " ".join(str(v or "").split())
    if required and not s:
        errors.append(f"{name} is required")
    if len(s) > limit:
        errors.append(f"{name} must be at most {limit} characters")
    return s[:limit]


def clean_plan(kind: str, plan, brief: str = "") -> dict:
    """Validate a plan (from the director or edited by the owner); raises CreatorError listing every problem.
    brief: the owner's description, which fills a description the director left out (gemma often does)."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        raise CreatorError("the plan must be an object")
    brief = " ".join(str(brief or "").split())
    if brief and not str(plan.get("description") or "").strip():
        plan = dict(plan, description=brief[:2000])
    if brief and isinstance(plan.get("voice"), dict) and plan["voice"].get("line") and not plan["voice"].get("description"):
        plan = dict(plan, voice=dict(plan["voice"], description=f"The voice of: {brief}"[:500]))
    out = {"name": _text(plan.get("name"), 120, "name", errors, required=True),
           "description": _text(plan.get("description"), 2000, "description", errors, required=True),
           "tags": _text(plan.get("tags"), 500, "tags", errors), "views": [], "voice": None, "music": None}
    views = plan.get("views") or []
    if not isinstance(views, list):
        errors.append("views must be a list of {label, prompt}")
        views = []
    if kind != "sound" and not views:
        errors.append("views needs at least one image: the first is the anchor, made from text")
    if len(views) > MAX_VIEWS:
        errors.append(f"at most {MAX_VIEWS} views")
    for i, v in enumerate(views[:MAX_VIEWS]):
        if not isinstance(v, dict):
            errors.append(f"views[{i}] must be an object {{label, prompt}}")
            continue
        out["views"].append({"label": _text(v.get("label"), 80, f"views[{i}].label", errors, required=True),
                             "prompt": _text(v.get("prompt"), 2000, f"views[{i}].prompt", errors, required=True)})
    voice = plan.get("voice")
    if kind == "character" and isinstance(voice, dict) and (voice.get("description") or voice.get("line")):
        out["voice"] = {"description": _text(voice.get("description"), 500, "voice.description", errors, required=True),
                        "line": _text(voice.get("line"), 300, "voice.line", errors, required=True)}
    music = plan.get("music")
    if kind == "sound":
        if not isinstance(music, dict) or not music.get("style"):
            errors.append("a sound needs music: {style, lyrics} (lyrics empty for instrumental)")
        else:
            seconds = music.get("seconds") or 30
            try:
                seconds = max(5, min(120, int(seconds)))
            except (TypeError, ValueError):
                errors.append("music.seconds must be a number")
                seconds = 30
            out["music"] = {"style": _text(music.get("style"), 500, "music.style", errors, required=True),
                            "lyrics": str(music.get("lyrics") or "").strip()[:3000], "seconds": seconds}
    if errors:
        raise CreatorError("; ".join(errors))
    return out


PROPOSE_ASSET = {"type": "function", "function": {
    "name": "propose_asset",
    "description": "Draft the library asset the owner described. Nothing is generated and nothing enters the library: "
                   "the owner reviews it, generates the images (and voice or music) and approves it.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "Required. Short library name, e.g. 'Anthony_science' or 'Jim's lab'"},
        "description": {"type": "string", "description": "Required. Who or what it is and what must stay the same in "
                                                         "every shot (face, hair, build, clothing; layout; shape)"},
        "tags": {"type": "string", "description": "Space-separated search tags"},
        "views": {"type": "array", "maxItems": MAX_VIEWS, "description": (
            "The reference images, first = the anchor. The anchor's prompt is a complete text-to-image prompt. Every "
            "later view is an edit of the kept anchor: say what changes (angle, pose, framing) and that everything "
            "else stays identical."), "items": {"type": "object", "properties": {
                "label": {"type": "string", "description": "e.g. front portrait, full body, three-quarter view"},
                "prompt": {"type": "string"}}}},
        "voice": {"type": "object", "description": "Characters only: the voice to design", "properties": {
            "description": {"type": "string", "description": "Required with a voice. e.g. 'young male android, bright, "
                                                             "curious, slightly synthetic, American English'"},
            "line": {"type": "string", "description": "A natural 8-15 second line in character to record the sample"}}},
        "music": {"type": "object", "description": "Sounds only (music): style and lyrics", "properties": {
            "style": {"type": "string", "description": "Genre, instruments, mood, tempo"},
            "lyrics": {"type": "string", "description": "Empty for instrumental; else [Verse]/[Chorus] sections"},
            "seconds": {"type": "integer", "minimum": 5, "maximum": 120}}}}}}}


def creator_brief(kind: str, brief: str, image_template: str, edit_template: str, voice: bool) -> str:
    lines = [f"Create a library {kind} from my description: “{' '.join(brief.split())}”",
             "Call propose_asset ONCE with the draft. Nothing is generated or added to the library until I approve."]
    if kind == "sound":
        lines.append("A sound is music made with a music model: give music.style (genre, instruments, mood, tempo), "
                     "music.lyrics (empty for instrumental) and music.seconds. No views.")
    else:
        lines += [f"Images: the anchor view is made from text with {image_template}; the other views are edits of the "
                  f"kept anchor with {edit_template}, so the {kind} stays identical. Plan these views: "
                  f"{VIEW_GUIDE[kind]}. 2-3 views is usually right.",
                  "Write the anchor as one dense image prompt: subject, every identifying detail (colours, materials, "
                  "clothing, age, build), framing, background and light. Write each later view as an edit: what "
                  "changes, and “keep the same face, hair, clothing and colours” (or the place's layout, the object's "
                  "shape).",
                  "The description says what must never change from shot to shot; the video model reads it."]
    if kind == "character" and voice:
        lines.append("Also design the voice: voice.description (age, gender, timbre, pace, accent, character) and "
                     "voice.line, a natural line in character of 8-15 seconds that shows the voice.")
    lines.append("Reply with one short sentence after the call.")
    return "\n".join(lines)


class Creator:
    def __init__(self, studio):
        self.s = studio
        self.db: Database = studio.db
        self.bus: EventBus = studio.bus
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self):
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # --- records ---------------------------------------------------------------

    async def get(self, creation_id: str) -> dict:
        row = await self.db.fetchone("SELECT * FROM creations WHERE id = ?", (creation_id,))
        if not row:
            raise CreatorError("no such creation")
        row["plan"] = json.loads(row["plan"]) if row["plan"] else None
        return row

    async def active(self) -> list[dict]:
        rows = await self.db.fetchall("SELECT * FROM creations WHERE status IN ('drafting', 'ready') ORDER BY created_at DESC")
        out = []
        for r in rows:
            r["plan"] = json.loads(r["plan"]) if r["plan"] else None
            out.append(r | {"asset": await self._summary(r["asset_id"])})
        return out

    async def _summary(self, asset_id: str) -> dict | None:
        try:
            a = await self.s.library.asset(asset_id)
        except LibraryError:
            return None
        return {"id": a["id"], "kind": a["kind"], "name": a["name"], "voice": a.get("voice"),
                "files": [f for v in a["variants"] for f in v["files"]]}

    async def _changed(self, creation_id: str):
        await self.bus.publish("creation.updated", {"creation_id": creation_id})

    async def _templates(self, image_template: str | None, edit_template: str | None) -> tuple[str, str]:
        cfg = await self.s.caps.settings()
        matrix = await self.s.caps.matrix()
        image_template = image_template or cfg["image_text"]
        edit_template = edit_template or cfg["image_refs"]
        try:
            self.s.caps.ensure_usable(matrix, image_template, ("gen_image",))
            entry = self.s.caps.ensure_usable(matrix, edit_template, ("edit_image",))
        except ValueError as exc:
            raise CreatorError(str(exc)) from exc
        if entry["refs"] == "none":
            raise CreatorError(f"{edit_template} takes no reference image; the other views need one that does")
        return image_template, edit_template

    async def _draft_asset(self, kind: str, name: str, actor: str) -> str:
        asset = await self.s.library.create_asset({"kind": kind, "name": name}, actor, draft=True)
        return asset["id"]

    # --- start -----------------------------------------------------------------

    async def start(self, kind: str, brief: str, actor: str, image_template: str | None = None,
                    edit_template: str | None = None, voice: bool = True) -> dict:
        """The owner's description → a draft asset and a creation the director drafts in the background."""
        if kind not in KINDS:
            raise CreatorError(f"kind must be one of {', '.join(KINDS)}")
        brief = " ".join(str(brief or "").split())
        if not 10 <= len(brief) <= 4000:
            raise CreatorError("describe it in a sentence or more (10-4000 characters)")
        image_template, edit_template = await self._templates(image_template, edit_template)
        asset_id = await self._draft_asset(kind, f"New {kind}", actor)
        cid, ts = new_id(), now()
        await self.db.execute(
            "INSERT INTO creations (id, asset_id, kind, brief, image_template, edit_template, status, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'drafting', ?, ?)",
            (cid, asset_id, kind, brief, image_template, edit_template, ts, ts))
        await self.db.audit(actor, "creation.start", cid, {"kind": kind})
        self._spawn(self._draft(cid, voice))
        await self._changed(cid)
        return await self.get(cid)

    async def redraft(self, creation_id: str, actor: str, voice: bool = True) -> dict:
        c = await self.get(creation_id)
        if c["status"] not in ACTIVE_STATES:
            raise CreatorError(f"this creation is {c['status']}")
        await self.db.execute("UPDATE creations SET status = 'drafting', error = NULL, updated_at = ? WHERE id = ?",
                              (now(), creation_id))
        await self.db.audit(actor, "creation.redraft", creation_id)
        self._spawn(self._draft(creation_id, voice))
        await self._changed(creation_id)
        return await self.get(creation_id)

    async def _draft(self, creation_id: str, voice: bool):
        """One director turn with only propose_asset: up to three rounds, errors go back to it to fix."""
        c = await self.get(creation_id)
        cfg = await self.s.director_settings.get()
        model = cfg.get("model") or None
        conversation = f"filmlab:creator:{creation_id}:{new_id()[:6]}"
        first = [{"role": "user", "content": creator_brief(c["kind"], c["brief"], c["image_template"],
                                                           c["edit_template"], voice)}]
        messages, error, fresh, reminded = first, "the director replied without drafting it; try again", False, False

        async def ignore(_chunk):
            return None
        for _ in range(4):
            try:
                result = await self.s.gateway.stream_turn(conversation, messages, [PROPOSE_ASSET], ignore, model=model)
            except asyncio.CancelledError:
                raise
            except (GatewayError, OSError) as exc:
                if EMPTY_REPLY in str(exc) and not fresh:  # a stuck session: once, start over in a fresh one
                    fresh, conversation, messages = True, f"{conversation}:r", first
                    continue
                error = f"director unavailable: {exc}"
                break
            calls = [t for t in result.tool_calls if t["name"] == "propose_asset"]
            if not calls:
                if reminded:
                    break
                reminded = True
                messages = [{"role": "user", "content": "Call propose_asset now with the draft; nothing is saved "
                                                        "until that call succeeds."}]
                continue
            call = calls[0]
            try:
                args = json.loads(call["arguments"] or "{}")
                if isinstance(args.get("propose_asset"), dict):  # nested under the tool's name
                    args = args["propose_asset"]
                plan = clean_plan(c["kind"], args, c["brief"])
            except (ValueError, CreatorError) as exc:
                cid = call["id"] or new_id()[:9]
                messages = [{"role": "assistant", "content": result.text or "", "tool_calls": [
                                {"id": cid, "type": "function", "function": {"name": "propose_asset",
                                                                              "arguments": call["arguments"] or "{}"}}]},
                            {"role": "tool", "tool_call_id": cid,
                             "content": json.dumps({"error": f"not saved; fix and call propose_asset again: {exc}"})}]
                error = str(exc)
                continue
            if not voice:
                plan["voice"] = None
            await self._set_plan(creation_id, plan, "director")
            return
        await self.db.execute("UPDATE creations SET status = 'ready', error = ?, updated_at = ? WHERE id = ?",
                              (error[:500], now(), creation_id))
        await self._changed(creation_id)

    async def _set_plan(self, creation_id: str, plan: dict, actor: str):
        c = await self.get(creation_id)
        await self.s.library.update_asset(c["asset_id"], {"name": plan["name"], "description": plan["description"],
                                                          "tags": plan["tags"]}, actor)
        await self.db.execute("UPDATE creations SET plan = ?, status = 'ready', error = NULL, updated_at = ? WHERE id = ?",
                              (json.dumps(plan), now(), creation_id))
        await self.db.audit(actor, "creation.plan", creation_id)
        await self._changed(creation_id)

    async def update_plan(self, creation_id: str, plan, actor: str) -> dict:
        """The owner edits the draft (name, description, prompts, voice, music) before or between generations."""
        c = await self.get(creation_id)
        if c["status"] != "ready":
            raise CreatorError("wait for the director's draft first" if c["status"] == "drafting" else f"this creation is {c['status']}")
        await self._set_plan(creation_id, clean_plan(c["kind"], plan), actor)
        return await self.get(creation_id)

    async def from_director(self, kind: str, plan_args: dict, brief: str) -> dict:
        """propose_asset from the project chat: a creation that is ready for the owner to review and generate."""
        if kind not in KINDS:
            raise CreatorError(f"kind must be one of {', '.join(KINDS)}")
        plan = clean_plan(kind, plan_args, brief)
        image_template, edit_template = await self._templates(None, None)
        asset_id = await self._draft_asset(kind, plan["name"], "director")
        cid, ts = new_id(), now()
        await self.db.execute(
            "INSERT INTO creations (id, asset_id, kind, brief, image_template, edit_template, status, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, ?)",
            (cid, asset_id, kind, (" ".join(str(brief or plan["description"]).split()))[:4000], image_template,
             edit_template, ts, ts))
        await self._set_plan(cid, plan, "director")
        return await self.get(cid)

    # --- generate --------------------------------------------------------------

    async def _variant(self, asset_id: str) -> str:
        a = await self.s.library.asset(asset_id)
        return a["variants"][0]["id"]

    async def anchor_file(self, asset_id: str) -> str | None:
        """The kept anchor: the first image in the draft's look."""
        a = await self.s.library.asset(asset_id)
        images = [f for v in a["variants"] for f in v["files"] if f["kind"] == "image"]
        return images[0]["file_id"] if images else None

    async def generate(self, creation_id: str, part: str, actor: str, count: int | None = None) -> list[dict]:
        """part: anchor (the first view, from text), views (the others, edits of the kept anchor), voice, music."""
        c = await self.get(creation_id)
        if c["status"] != "ready" or not c["plan"]:
            raise CreatorError("wait for the draft first" if c["status"] == "drafting" else f"this creation is {c['status']}")
        plan, variant = c["plan"], await self._variant(c["asset_id"])
        cfg = await self.s.caps.settings()
        jobs = []
        if part == "anchor":
            if not plan["views"]:
                raise CreatorError("this draft has no images to generate")
            v = plan["views"][0]
            jobs.append(await self.s.images.submit(variant, v["prompt"], actor, c["image_template"], count=count,
                                                   label=v["label"]))
        elif part == "views":
            anchor = await self.anchor_file(c["asset_id"])
            if not anchor:
                raise CreatorError("keep an anchor image first: the other views are made from it")
            for v in plan["views"][1:]:
                jobs.append(await self.s.images.submit(variant, v["prompt"], actor, c["edit_template"], [anchor],
                                                       count=count or 1, label=v["label"]))
            if not jobs:
                raise CreatorError("this draft has only the anchor view")
        elif part == "voice":
            if c["kind"] != "character" or not plan.get("voice"):
                raise CreatorError("this draft has no voice to design")
            jobs.append(await self.s.images.submit(
                variant, plan["voice"]["line"], actor, cfg["voice_design"], count=count or 2, label="voice", media="audio",
                extra={"alt_prompt": plan["voice"]["description"], "duration_seconds": 20}))
        elif part == "music":
            if not plan.get("music"):
                raise CreatorError("this draft has no music")
            m = plan["music"]
            jobs.append(await self.s.images.submit(
                variant, m["lyrics"] or "[Instrumental]", actor, cfg["music"], count=count or 1, label="music",
                media="audio", extra={"alt_prompt": m["style"], "duration_seconds": m["seconds"]}))
        else:
            raise CreatorError("part must be anchor, views, voice or music")
        await self.db.execute("UPDATE creations SET updated_at = ? WHERE id = ?", (now(), creation_id))
        await self._changed(creation_id)
        return jobs

    # --- finish ----------------------------------------------------------------

    async def approve(self, creation_id: str, actor: str) -> dict:
        c = await self.get(creation_id)
        if c["status"] != "ready":
            raise CreatorError(f"this creation is {c['status']}")
        a = await self.s.library.asset(c["asset_id"])
        files = [f for v in a["variants"] for f in v["files"]]
        if c["kind"] == "sound" and not any(f["kind"] == "audio" for f in files):
            raise CreatorError("keep at least one music take first")
        if c["kind"] != "sound" and not any(f["kind"] == "image" for f in files):
            raise CreatorError("keep at least one image first")
        await self.db.execute("UPDATE assets SET draft = 0, updated_at = ? WHERE id = ?", (now(), c["asset_id"]))
        await self.db.execute("UPDATE creations SET status = 'approved', approved_at = ?, updated_at = ? WHERE id = ?",
                              (now(), now(), creation_id))
        await self.db.audit(actor, "creation.approve", creation_id, {"asset_id": c["asset_id"]})
        await self.bus.publish("library.updated", {"asset_id": c["asset_id"]})
        await self._changed(creation_id)
        return await self.get(creation_id)

    async def discard(self, creation_id: str, actor: str) -> dict:
        c = await self.get(creation_id)
        if c["status"] not in ACTIVE_STATES:
            raise CreatorError(f"this creation is {c['status']}")
        await self.s.trash.delete_asset(c["asset_id"], actor)  # refuses while something is still generating
        await self.db.execute("UPDATE creations SET status = 'discarded', updated_at = ? WHERE id = ?", (now(), creation_id))
        await self.db.audit(actor, "creation.discard", creation_id)
        await self._changed(creation_id)
        return await self.get(creation_id)
