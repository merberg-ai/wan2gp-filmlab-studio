"""Shot rules shared by the cockpit API and the director's tools."""

import json
import re

from .db import Database, new_id, now
from .events import EventBus
from .media import snap_frames
from .sound import SoundError, clean_sound

# A rendered ("ready") shot stays editable: the next take uses the new version, earlier takes keep
# their own record of what was rendered. Approved and rendering shots are locked.
EDITABLE = ("draft", "failed", "pending_approval", "ready")
RESOLUTION = re.compile(r"^(\d{3,4})x(\d{3,4})$")
# The transition into the next shot, and the ffmpeg xfade effect the export uses for it (a cut has none).
# The cockpit's labels for these are in frontend/src/api.ts (TRANSITION_GROUPS).
TRANSITIONS = {
    "cut": None, "fade": "fadeblack", "dissolve": "fade", "fade_white": "fadewhite",
    "wipe_left": "wipeleft", "wipe_right": "wiperight", "wipe_up": "wipeup", "wipe_down": "wipedown",
    "soft_wipe_left": "smoothleft", "soft_wipe_right": "smoothright",
    "slide_left": "slideleft", "slide_right": "slideright", "slide_up": "slideup", "slide_down": "slidedown",
    "circle_open": "circleopen", "circle_close": "circleclose", "clock_wipe": "radial",
    "zoom_in": "zoomin", "blur": "hblur", "pixelize": "pixelize", "squeeze": "squeezeh",
}
# What a revision records: everything that decides what a shot renders (plus its references).
TRACKED = ("title", "idea", "prompt", "frames", "resolution", "template", "template_explicit", "seed", "transition",
           "sound")
MAX_PIXELS = 1920 * 1088


class ShotError(ValueError):
    pass


def clean_fields(fields: dict, partial: bool) -> dict:
    out = {}
    if "title" in fields or not partial:
        title = str(fields.get("title") or "").strip()
        if not 1 <= len(title) <= 120:
            raise ShotError("title must be 1-120 characters")
        out["title"] = title
    if "description" in fields:
        out["description"] = str(fields["description"] or "")[:2000]
    if "prompt" in fields or not partial:
        prompt = str(fields.get("prompt") or "").strip()
        if not 10 <= len(prompt) <= 4000:
            raise ShotError("prompt must be 10-4000 characters")
        out["prompt"] = prompt
    if "idea" in fields and fields["idea"] is not None:
        out["idea"] = str(fields["idea"]).strip()[:4000]
    if "frames" in fields and fields["frames"] is not None:
        out["frames"] = snap_frames(int(fields["frames"]))
    if "resolution" in fields and fields["resolution"]:
        m = RESOLUTION.match(str(fields["resolution"]))
        if not m or int(m[1]) * int(m[2]) > MAX_PIXELS:
            raise ShotError("resolution must look like 1280x720 and be at most ~1920x1088")
        out["resolution"] = str(fields["resolution"])
    if "seed" in fields and fields["seed"] is not None:
        out["seed"] = int(fields["seed"])
    if "transition" in fields and fields["transition"]:
        if fields["transition"] not in TRANSITIONS:
            raise ShotError(f"transition must be one of {', '.join(TRANSITIONS)}")
        out["transition"] = fields["transition"]
    if "sound" in fields:
        try:
            sound = clean_sound(fields["sound"])
        except SoundError as exc:
            raise ShotError(str(exc)) from None
        out["sound"] = json.dumps(sound) if sound is not None else None
    return out


async def snapshot(db: Database, shot_id: str) -> dict:
    """The tracked state of a shot, for its revision log."""
    shot = await db.fetchone("SELECT * FROM shots WHERE id = ?", (shot_id,))
    refs = await db.fetchall("SELECT variant_id, file_id, role FROM shot_refs WHERE shot_id = ? ORDER BY position",
                             (shot_id,))
    return {k: shot[k] for k in TRACKED} | {"refs": refs}


async def record(db: Database, shot_id: str, actor: str, kind: str, before: dict, rationale: str = "") -> dict | None:
    """Log a change to a shot (who, what, why). Nothing is logged when nothing tracked changed."""
    after = await snapshot(db, shot_id)
    if kind != "create" and before == after:
        return None
    rev = {"id": new_id(), "shot_id": shot_id, "actor": actor, "kind": kind, "rationale": str(rationale or "")[:1000],
           "before": json.dumps(before), "after": json.dumps(after), "created_at": now()}
    await db.execute("INSERT INTO shot_revisions (id, shot_id, actor, kind, before, after, rationale, created_at) "
                     "VALUES (:id, :shot_id, :actor, :kind, :before, :after, :rationale, :created_at)", rev)
    if rationale:
        await db.execute("UPDATE shots SET rationale = ? WHERE id = ?", (rev["rationale"], shot_id))
    return rev


async def revisions(db: Database, shot_id: str) -> list[dict]:
    rows = await db.fetchall("SELECT * FROM shot_revisions WHERE shot_id = ? ORDER BY created_at, rowid", (shot_id,))
    for r in rows:
        r["before"], r["after"] = json.loads(r["before"]), json.loads(r["after"])
        r["changed"] = sorted(k for k in set(r["before"]) | set(r["after"]) if r["before"].get(k) != r["after"].get(k))
    return rows


async def get_shot(db: Database, shot_id: str, project_id: str | None = None) -> dict:
    shot = await db.fetchone("SELECT * FROM shots WHERE id = ? AND deleted_at IS NULL", (shot_id,))
    if not shot or (project_id and shot["project_id"] != project_id):
        raise ShotError("no such shot in this project")
    return shot


async def create_shot(db: Database, bus: EventBus, project_id: str, fields: dict, template: dict, actor: str,
                      explicit: bool = True, scene_id: str | None = None, rationale: str = "") -> dict:
    """explicit=False: no template was chosen, so the shot follows the configured defaults."""
    data = clean_fields(fields, partial=False)
    project = await db.fetchone("SELECT default_resolution, default_frames FROM projects WHERE id = ?", (project_id,))
    pos = await db.fetchone("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM shots WHERE project_id = ?", (project_id,))
    shot_id, ts = new_id(), now()
    await db.execute(
        "INSERT INTO shots (id, project_id, position, title, description, prompt, template, model_type, resolution, "
        "frames, seed, status, created_by, created_at, updated_at, template_explicit, idea, scene_id, transition, sound) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)",
        (shot_id, project_id, pos["p"], data["title"], data.get("description", ""), data["prompt"],
         template["template"], template["model_type"], data.get("resolution") or project["default_resolution"],
         data.get("frames") or project["default_frames"], data.get("seed", -1), actor, ts, ts, int(explicit),
         data.get("idea") or (data["prompt"] if actor == "owner" else ""), scene_id, data.get("transition", "cut"),
         data.get("sound")))
    await db.audit(actor, "shot.create", shot_id)
    await record(db, shot_id, actor, "create", {}, rationale)
    shot = await get_shot(db, shot_id)
    await bus.publish("shot.updated", {"shot_id": shot_id, "status": "draft", "created": True}, project_id)
    return shot


async def update_shot(db: Database, bus: EventBus, shot: dict, fields: dict, actor: str, template: dict | None = None,
                      follow_defaults: bool = False, rationale: str = "", kind: str = "update") -> dict:
    if shot["status"] not in EDITABLE:
        raise ShotError(f"shot is {shot['status']}; it can't be edited while it is approved or rendering")
    if actor == "director" and shot["locked"]:
        raise ShotError(f"shot {shot['position']} is locked by the owner; propose the change in chat instead")
    before = await snapshot(db, shot["id"])
    data = clean_fields(fields, partial=True)
    if template:
        data.update(template=template["template"], model_type=template["model_type"],
                    template_explicit=0 if follow_defaults else 1)
    if not data:
        return shot
    status = "ready" if shot["status"] == "ready" else "draft"  # an edit withdraws a pending approval;
    data["status"] = status                                      # a rendered shot keeps its takes
    sets = ", ".join(f"{k} = ?" for k in data)
    await db.execute(f"UPDATE shots SET {sets}, updated_at = ? WHERE id = ?", (*data.values(), now(), shot["id"]))
    await db.execute("UPDATE approvals SET status = 'withdrawn', decided_at = ? WHERE shot_id = ? AND status = 'pending'",
                     (now(), shot["id"]))
    await db.audit(actor, "shot.update", shot["id"], sorted(data))
    await record(db, shot["id"], actor, kind, before, rationale)
    await bus.publish("shot.updated", {"shot_id": shot["id"], "status": status}, shot["project_id"])
    return await get_shot(db, shot["id"])


async def request_approval(db: Database, bus: EventBus, shot: dict, actor: str, note: str = "") -> dict:
    if shot["status"] not in ("draft", "failed"):
        raise ShotError(f"shot is {shot['status']}; it cannot be submitted for approval")
    approval_id = new_id()

    def write(c):  # together, so no reader sees the approval on a shot that is still a draft
        c.execute("INSERT INTO approvals (id, project_id, shot_id, kind, requested_by, note, created_at) "
                  "VALUES (?, ?, ?, 'render', ?, ?, ?)", (approval_id, shot["project_id"], shot["id"], actor, note[:500], now()))
        c.execute("UPDATE shots SET status = 'pending_approval', updated_at = ? WHERE id = ?", (now(), shot["id"]))
    await db.transaction(write)
    await db.audit(actor, "approval.request", shot["id"], {"approval_id": approval_id})
    await bus.publish("approval.created", {"approval_id": approval_id, "shot_id": shot["id"], "requested_by": actor,
                                           "note": note[:500]}, shot["project_id"])
    await bus.publish("shot.updated", {"shot_id": shot["id"], "status": "pending_approval"}, shot["project_id"])
    return {"approval_id": approval_id, "status": "pending"}


async def approve(db: Database, bus: EventBus, shot: dict, actor: str) -> dict:
    """Owner-only: approve a draft/pending/failed shot so it may render. An already approved shot (its render was
    cancelled before a take existed) stays approved, so it can be rendered again; submit() refuses a second job."""
    if shot["status"] not in ("draft", "pending_approval", "failed", "ready", "approved"):
        raise ShotError(f"shot is {shot['status']}")
    await db.execute("UPDATE approvals SET status = 'approved', decided_at = ? WHERE shot_id = ? AND status = 'pending'",
                     (now(), shot["id"]))
    await db.execute("UPDATE shots SET status = 'approved', updated_at = ? WHERE id = ?", (now(), shot["id"]))
    await db.audit(actor, "shot.approve", shot["id"])
    await bus.publish("shot.updated", {"shot_id": shot["id"], "status": "approved"}, shot["project_id"])
    return await get_shot(db, shot["id"])


async def undo_approve(db: Database, bus: EventBus, before: dict, actor: str):
    """Undo approve() when the render was refused before any job was created for it."""
    shot = await get_shot(db, before["id"])
    if shot["status"] != "approved" or before["status"] == "approved":
        return  # a job took over (it decides the outcome), or it was already approved
    if await db.fetchone("SELECT 1 FROM render_jobs WHERE shot_id = ? AND submitted_at >= ?",
                         (shot["id"], shot["updated_at"])):
        return
    await db.execute("UPDATE shots SET status = ?, updated_at = ? WHERE id = ?", (before["status"], now(), shot["id"]))
    if before["status"] == "pending_approval":  # re-open the request approve() closed
        await db.execute(
            "UPDATE approvals SET status = 'pending', decided_at = NULL WHERE id = (SELECT id FROM approvals "
            "WHERE shot_id = ? AND status = 'approved' ORDER BY decided_at DESC LIMIT 1)", (shot["id"],))
    await db.audit(actor, "shot.approve_undone", shot["id"], {"status": before["status"]})
    await bus.publish("shot.updated", {"shot_id": shot["id"], "status": before["status"]}, shot["project_id"])


REFS_EDITABLE = EDITABLE + ("approved",)


async def refs_changed(db: Database, bus: EventBus, shot: dict, actor: str):
    """References are part of what gets rendered: changing them withdraws a pending approval."""
    if shot["status"] == "pending_approval":
        await db.execute("UPDATE approvals SET status = 'withdrawn', decided_at = ? WHERE shot_id = ? AND status = 'pending'",
                         (now(), shot["id"]))
        await db.execute("UPDATE shots SET status = 'draft', updated_at = ? WHERE id = ?", (now(), shot["id"]))
    else:
        await db.execute("UPDATE shots SET updated_at = ? WHERE id = ?", (now(), shot["id"]))
    status = "draft" if shot["status"] == "pending_approval" else shot["status"]
    await bus.publish("shot.updated", {"shot_id": shot["id"], "status": status, "refs": True}, shot["project_id"])


async def reject(db: Database, bus: EventBus, shot: dict, actor: str) -> dict:
    if shot["status"] != "pending_approval":
        raise ShotError("nothing pending for this shot")
    await db.execute("UPDATE approvals SET status = 'rejected', decided_at = ? WHERE shot_id = ? AND status = 'pending'",
                     (now(), shot["id"]))
    await db.execute("UPDATE shots SET status = 'draft', updated_at = ? WHERE id = ?", (now(), shot["id"]))
    await db.audit(actor, "shot.reject", shot["id"])
    await bus.publish("shot.updated", {"shot_id": shot["id"], "status": "draft"}, shot["project_id"])
    return await get_shot(db, shot["id"])
