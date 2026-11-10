"""F4 routes: brief, scenes, storyboard order and locks, shot history, planning with the director, batches."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, bulk, plan_check, shots, sound
from .batches import BatchError
from .db import now
from .director.chat import ChatBusy
from .director.planning import PLAN_TOOLS, plan_brief
from .renders import RenderError, summarize_queue
from .storyboard import StoryboardError
from .trash import TrashError

router = APIRouter(dependencies=[Depends(auth.require_owner)])
OWNER = "owner"
# Planning tool rounds: Settings → Director (default 14: list assets, look at a few, plan, maybe fix and plan again)


def st(request: Request):
    return request.app.state.studio


def bad(exc: Exception) -> HTTPException:
    return HTTPException(422 if isinstance(exc, (shots.ShotError, StoryboardError)) else 409, str(exc))


async def live_project(s, project_id: str) -> dict:
    p = await s.db.fetchone("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if not p:
        raise HTTPException(404, "no such project")
    return p


async def live_shot(s, shot_id: str) -> dict:
    try:
        return await shots.get_shot(s.db, shot_id)
    except shots.ShotError:
        raise HTTPException(404, "no such shot") from None


# --- scenes -------------------------------------------------------------------

class SceneIn(BaseModel):
    title: str | None = None
    goal: str | None = None
    time_place: str | None = None
    mood: str | None = None
    location_asset_id: str | None = None


class MoveIn(BaseModel):
    scene_id: str | None = None
    index: int | None = None


@router.post("/api/projects/{project_id}/scenes", status_code=201)
async def create_scene(project_id: str, body: SceneIn, request: Request):
    s = st(request)
    await live_project(s, project_id)
    try:
        return await s.storyboard.create_scene(project_id, body.model_dump(exclude_none=True), OWNER)
    except StoryboardError as exc:
        raise bad(exc) from exc


@router.patch("/api/scenes/{scene_id}")
async def update_scene(scene_id: str, body: SceneIn, request: Request):
    try:
        return await st(request).storyboard.update_scene(scene_id, body.model_dump(exclude_unset=True), OWNER)
    except StoryboardError as exc:
        raise bad(exc) from exc


@router.post("/api/scenes/{scene_id}/move")
async def move_scene(scene_id: str, body: MoveIn, request: Request):
    try:
        return await st(request).storyboard.move_scene(scene_id, body.index or 0, OWNER)
    except StoryboardError as exc:
        raise bad(exc) from exc


@router.delete("/api/scenes/{scene_id}")
async def delete_scene(scene_id: str, request: Request):
    s = st(request)
    try:
        return await s.storyboard.delete_scene(scene_id, s.trash, OWNER)
    except (StoryboardError, TrashError) as exc:
        raise bad(exc) from exc


# --- shots: order, lock, history ---------------------------------------------------

class LockIn(BaseModel):
    locked: bool


@router.post("/api/shots/{shot_id}/move")
async def move_shot(shot_id: str, body: MoveIn, request: Request):
    s = st(request)
    await live_shot(s, shot_id)
    try:
        return await s.storyboard.move_shot(shot_id, body.scene_id, body.index, OWNER)
    except (StoryboardError, shots.ShotError) as exc:
        raise bad(exc) from exc


@router.post("/api/shots/{shot_id}/lock")
async def lock_shot(shot_id: str, body: LockIn, request: Request):
    s = st(request)
    await live_shot(s, shot_id)
    return await s.storyboard.set_locked(shot_id, body.locked, OWNER)


class BulkIn(BaseModel):
    action: str
    shot_ids: list[str] = Field(default_factory=list, max_length=200)
    scene_ids: list[str] = Field(default_factory=list, max_length=50)
    fields: dict = Field(default_factory=dict)  # for "set": template ("" = follow the defaults), resolution, frames


class UndoIn(BaseModel):
    token: str


@router.post("/api/projects/{project_id}/bulk")
async def bulk_action(project_id: str, body: BulkIn, request: Request):
    """Delete, lock, unlock or change several shots (or whole scenes) at once; a delete can be undone."""
    s = st(request)
    await live_project(s, project_id)
    try:
        return await bulk.run(s, project_id, body.shot_ids, body.scene_ids, body.action, body.fields, OWNER)
    except (bulk.BulkError, shots.ShotError, RenderError) as exc:
        raise bad(exc) from exc


@router.post("/api/projects/{project_id}/bulk/undo")
async def bulk_undo(project_id: str, body: UndoIn, request: Request):
    s = st(request)
    await live_project(s, project_id)
    try:
        return await bulk.undo(s, project_id, body.token, OWNER)
    except bulk.BulkError as exc:
        raise bad(exc) from exc


@router.get("/api/shots/{shot_id}/revisions")
async def shot_revisions(shot_id: str, request: Request):
    """The shot's history, each version with the takes rendered from it (submitted before the next change)."""
    s = st(request)
    await live_shot(s, shot_id)
    revs = await shots.revisions(s.db, shot_id)
    takes = await s.db.fetchall(
        "SELECT t.id, t.media_id, t.thumb_media_id, t.verdict, t.preview, j.submitted_at FROM takes t "
        "JOIN render_jobs j ON j.id = t.job_id WHERE t.shot_id = ? AND t.deleted_at IS NULL ORDER BY j.submitted_at",
        (shot_id,))
    for i, r in enumerate(revs):
        until = revs[i + 1]["created_at"] if i + 1 < len(revs) else None
        r["takes"] = [t | {"preview": bool(t["preview"])} for t in takes
                      if t["submitted_at"] >= r["created_at"] and (until is None or t["submitted_at"] < until)]
    return revs


@router.post("/api/revisions/{revision_id}/restore")
async def restore_revision(revision_id: str, request: Request):
    s = st(request)
    try:
        return await s.storyboard.restore_revision(revision_id, OWNER)
    except (StoryboardError, shots.ShotError, RenderError) as exc:
        raise bad(exc) from exc


# --- planning with the director ----------------------------------------------

class PlanIn(BaseModel):
    replace_drafts: bool = False


PLAN_PARTS = 8  # a scene or two per part; storyboards hold at most 12 scenes


@router.get("/api/projects/{project_id}/plan-check")
async def check_plan(project_id: str, request: Request):
    """What in the brief disagrees with the project's settings (length, style, Library names), before planning."""
    s = st(request)
    project = await live_project(s, project_id)
    return {"issues": plan_check.check(project, await s.library.search("", None, limit=500))}


@router.post("/api/projects/{project_id}/plan", status_code=202)
async def plan(project_id: str, body: PlanIn, request: Request):
    """Send the director the brief, the library and the storyboard; it answers with plan_storyboard."""
    s = st(request)
    project = await live_project(s, project_id)
    if len(project["brief"].strip()) < 10:
        raise HTTPException(422, "write a brief first (at least a sentence)")
    text = await plan_brief(s, project, replace=body.replace_drafts)

    async def resume() -> str:  # after an empty reply: carry on from the parts already saved, as Plan more does
        return await plan_brief(s, await live_project(s, project_id), replace=False)

    try:
        rounds = (await s.director_settings.get())["plan_rounds"]
        # a plan comes in parts of a scene or two (about three rounds each): room for a long film
        turn = await s.chat.send(project_id, text, max_rounds=max(rounds, 3 * PLAN_PARTS + 2),
                                 expect=("plan_storyboard", "a plan"), parts=PLAN_PARTS, resume=resume,
                                 tools=PLAN_TOOLS)
    except ChatBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    await s.db.audit(OWNER, "storyboard.plan_request", project_id, {"turn_id": turn, "replace": body.replace_drafts})
    return {"turn_id": turn}


# --- batches ------------------------------------------------------------------------

class BatchIn(BaseModel):
    shot_ids: list[str] = Field(min_length=1, max_length=60)
    request_id: str | None = None  # approving the director's request
    preview: bool = False  # quick low-resolution takes first
    start_at: str | None = None  # ISO time to start at (render overnight)


@router.get("/api/projects/{project_id}/batch-review")
async def batch_review(project_id: str, request: Request):
    """Every shot in storyboard order with what a batch would do with it, its pre-render checks, and the
    per-model rates this machine's past renders give (for a grounded estimate)."""
    s = st(request)
    project = await live_project(s, project_id)
    order = await s.storyboard.order(project_id)
    rows = []
    for sid in order:
        shot = await shots.get_shot(s.db, sid)
        refs = await s.library.shot_refs(sid)
        n_refs = len(refs)
        takes = (await s.db.fetchone("SELECT COUNT(*) AS n FROM takes WHERE shot_id = ?", (sid,)))["n"]
        template = await s.caps.effective_template(shot, project, n_refs)
        if (song := await s.music.song_template(shot, bool(n_refs))) is not None:
            template = song["template"]  # a lip-sync music video: the song-driven model renders it
        try:
            snd = sound.load(shot["sound"])
            issues = await s.caps.checks(template, n_refs, shot["prompt"], sound_fields=snd, refs=refs,
                                         voiced=await s.refs.voiced(snd, refs))
        except Exception as exc:  # noqa: BLE001 - WanGP not reachable: the review still opens
            issues = [{"level": "warn", "message": f"couldn't check with WanGP ({type(exc).__name__})"}]
        renderable = shot["status"] in ("draft", "pending_approval", "failed", "ready", "approved")
        blocked = any(i["level"] == "block" for i in issues)
        rows.append({"shot_id": sid, "position": shot["position"], "title": shot["title"], "status": shot["status"],
                     "locked": bool(shot["locked"]), "takes": takes, "template": template, "frames": shot["frames"],
                     "model_type": shot["model_type"], "issues": issues, "renderable": renderable and not shot["locked"],
                     "blocked": blocked,
                     "default": renderable and not shot["locked"] and not takes and not blocked})
    rates = await s.storyboard.rates()
    queue = None
    if s.wangp_ready.is_set():
        try:
            queue = summarize_queue(await s.wangp.session("list_queue"), s.wangp.agent_context)
        except Exception:  # noqa: BLE001
            queue = None
    return {"shots": rows, "rates": rates, "queue": queue}


@router.post("/api/projects/{project_id}/batches", status_code=201)
async def create_batch(project_id: str, body: BatchIn, request: Request):
    s = st(request)
    await live_project(s, project_id)
    if not s.wangp_ready.is_set():
        raise HTTPException(503, "WanGP is not connected; start WanGP with MCP enabled")
    try:
        batch = await s.batches.create(project_id, body.shot_ids, OWNER, preview=body.preview, start_at=body.start_at)
    except (BatchError, shots.ShotError) as exc:
        raise bad(exc) from exc
    if body.request_id:
        await s.db.execute("UPDATE batch_requests SET status = 'approved', decided_at = ? WHERE id = ? AND project_id = ? "
                           "AND status = 'pending'", (now(), body.request_id, project_id))
    return batch


@router.post("/api/batches/{batch_id}/{action}")
async def batch_action(batch_id: str, action: str, request: Request):
    s = st(request)
    fn = {"pause": s.batches.pause, "resume": s.batches.resume, "cancel": s.batches.cancel}.get(action)
    if not fn:
        raise HTTPException(404, "unknown batch action")
    try:
        return await fn(batch_id, OWNER)
    except (BatchError, RenderError) as exc:
        raise bad(exc) from exc


@router.post("/api/batches/{batch_id}/items/{position}/retry")
async def batch_retry(batch_id: str, position: int, request: Request):
    try:
        return await st(request).batches.retry(batch_id, position, OWNER)
    except (BatchError, RenderError) as exc:
        raise bad(exc) from exc


class MoveItemIn(BaseModel):
    direction: str  # up | down


@router.post("/api/batches/{batch_id}/items/{position}/move")
async def batch_move(batch_id: str, position: int, body: MoveItemIn, request: Request):
    """Render a waiting shot sooner or later."""
    try:
        return await st(request).batches.move(batch_id, position, body.direction, OWNER)
    except BatchError as exc:
        raise bad(exc) from exc


@router.post("/api/batch-requests/{request_id}/reject")
async def reject_batch_request(request_id: str, request: Request):
    s = st(request)
    n = await s.db.run(lambda c: c.execute("UPDATE batch_requests SET status = 'rejected', decided_at = ? WHERE id = ? "
                                           "AND status = 'pending'", (now(), request_id)).rowcount)
    if not n:
        raise HTTPException(404, "no pending request")
    await s.db.audit(OWNER, "batch_request.reject", request_id)
    return {"rejected": request_id}


async def project_extras(s, project_id: str) -> dict:
    """What the project view adds for F4: scenes, the current batch, pending director batch requests."""
    reqs = await s.db.fetchall("SELECT * FROM batch_requests WHERE project_id = ? AND status = 'pending' "
                               "ORDER BY created_at", (project_id,))
    for r in reqs:
        r["shot_ids"] = json.loads(r["shot_ids"])
    return {"scenes": await s.storyboard.scenes(project_id), "batch": await s.batches.latest(project_id),
            "batch_requests": reqs, "render_rates": await s.storyboard.rates(),
            "planning": s.chat.planning.get(project_id), "enhancing": s.enhance_all.state(project_id)}
