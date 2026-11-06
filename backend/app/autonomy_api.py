"""F6 routes: take inspections, redo proposals, bounded redo runs, autonomy settings and the GPU hold.
Every route here is the owner's; the director reaches inspections and proposals only through its tools."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import auth, shots
from .inspect_takes import InspectError
from .runs import RunError

router = APIRouter(dependencies=[Depends(auth.require_owner)])
OWNER = "owner"


def st(request: Request):
    return request.app.state.studio


def bad(exc: Exception) -> HTTPException:
    return HTTPException(422 if isinstance(exc, (InspectError, shots.ShotError)) else 409, str(exc))


async def take_or_404(s, take_id: str) -> dict:
    t = await s.db.fetchone("SELECT t.id, s.project_id FROM takes t JOIN shots s ON s.id = t.shot_id WHERE t.id = ?",
                            (take_id,))
    if not t:
        raise HTTPException(404, "no such take")
    return t


# --- inspections ----------------------------------------------------------------------

@router.get("/api/takes/{take_id}/inspection")
async def get_inspection(take_id: str, request: Request):
    s = st(request)
    await take_or_404(s, take_id)
    return {"inspection": await s.inspector.get(take_id)}


@router.post("/api/takes/{take_id}/inspect", status_code=202)
async def inspect_take(take_id: str, request: Request):
    s = st(request)
    await take_or_404(s, take_id)
    s.inspector.ensure(take_id, again=True)
    await s.db.audit(OWNER, "take.inspect", take_id)
    return {"take_id": take_id, "status": "running"}


@router.get("/api/takes/{take_id}/sheet")
async def contact_sheet(take_id: str, request: Request):
    s = st(request)
    await take_or_404(s, take_id)
    insp = await s.inspector.get(take_id)
    path = insp and insp["sheet_rel"] and (s.settings.media_dir / insp["sheet_rel"])
    if not path or not path.is_file():
        raise HTTPException(404, "no contact sheet yet")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=60"})


# --- redo proposals and runs ----------------------------------------------------------

class RunIn(BaseModel):
    shot_ids: list[str] | None = None
    caps: dict | None = None


async def project_extras(s, project_id: str) -> dict:
    """What the cockpit's project view shows for F6: each take's inspection, proposals, the run, the hold."""
    rows = await s.db.fetchall(
        "SELECT i.take_id, i.status, i.verdict, i.summary, i.sheet_rel IS NOT NULL AS sheet, i.vision FROM take_inspections i "
        "JOIN takes t ON t.id = i.take_id JOIN shots sh ON sh.id = t.shot_id WHERE sh.project_id = ?", (project_id,))
    inspections = {}
    for r in rows:
        v = json.loads(r.pop("vision") or "null") or {}
        inspections[r.pop("take_id")] = r | {"sheet": bool(r["sheet"]), "vision_error": v.get("error"),
                                             "vision_summary": v.get("summary")}
    return {"inspections": inspections, "redo": await redo_state_for(s, project_id)}


async def redo_state_for(s, project_id: str) -> dict:
    needing = []
    for sid in await s.storyboard.order(project_id):
        try:
            shot = await shots.get_shot(s.db, sid)
        except shots.ShotError:  # deleted while we read (a re-plan, say)
            continue
        need, why, take = await s.runs.needs_work(shot)
        if need:
            needing.append({"shot_id": sid, "position": shot["position"], "title": shot["title"], "why": why,
                            "take_id": take and take["id"]})
    return {"proposals": await s.runs.proposals(project_id), "run": await s.runs.latest(project_id),
            "needs_redo": needing, "hold": await s.gpu_held()}


@router.get("/api/projects/{project_id}/redo")
async def redo_state(project_id: str, request: Request):
    s = st(request)
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")
    return await redo_state_for(s, project_id)


@router.post("/api/redo-proposals/{proposal_id}/accept")
async def accept_proposal(proposal_id: str, request: Request):
    try:
        return await st(request).runs.accept(proposal_id, OWNER)
    except RunError as exc:
        raise bad(exc) from exc


@router.post("/api/redo-proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str, request: Request):
    try:
        return await st(request).runs.reject(proposal_id, OWNER)
    except RunError as exc:
        raise bad(exc) from exc


@router.post("/api/projects/{project_id}/runs", status_code=201)
async def start_run(project_id: str, body: RunIn, request: Request):
    try:
        return await st(request).runs.start(project_id, body.shot_ids, body.caps, OWNER)
    except RunError as exc:
        raise bad(exc) from exc


@router.post("/api/runs/{run_id}/{action}")
async def run_action(run_id: str, action: str, request: Request):
    runs = st(request).runs
    fn = {"pause": runs.pause, "resume": runs.resume, "stop": runs.stop}.get(action)
    if not fn:
        raise HTTPException(404, "unknown action")
    try:
        return await fn(run_id, OWNER)
    except RunError as exc:
        raise bad(exc) from exc


# --- settings and the GPU hold --------------------------------------------------------

class AutonomySettingsIn(BaseModel):
    auto_inspect: bool | None = None
    vision_model: str | None = Field(default=None, max_length=100)


class HoldIn(BaseModel):
    on: bool
    cancel: bool = False


@router.get("/api/settings/autonomy")
async def get_autonomy_settings(request: Request):
    return await st(request).inspector.settings()


@router.put("/api/settings/autonomy")
async def put_autonomy_settings(body: AutonomySettingsIn, request: Request):
    s = st(request)
    try:
        out = await s.inspector.set_settings(body.model_dump(exclude_none=True))
    except InspectError as exc:
        raise bad(exc) from exc
    await s.db.audit(OWNER, "settings.autonomy", None, body.model_dump(exclude_none=True))
    return out


@router.get("/api/gpu-hold")
async def get_hold(request: Request):
    s = st(request)
    return {"on": bool(await s.gpu_held()), "reason": await s.gpu_held()}


@router.post("/api/gpu-hold")
async def set_hold(body: HoldIn, request: Request):
    return await st(request).set_gpu_hold(body.on, body.cancel, OWNER)
