"""F5 routes: take review, the edit, exports and deliveries, waveforms and filmstrips."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import auth, shots
from .edit import EditError
from .export import ExportError, filmstrip, waveform
from .library import LibraryError

router = APIRouter(dependencies=[Depends(auth.require_owner)])
OWNER = "owner"


def st(request: Request):
    return request.app.state.studio


def bad(exc: Exception) -> HTTPException:
    return HTTPException(422 if isinstance(exc, (EditError, shots.ShotError)) else 409, str(exc))


async def live_project(s, project_id: str):
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")


# --- take review --------------------------------------------------------------------

class ReviewIn(BaseModel):
    verdict: str | None = None
    notes: str | None = None


class SelectIn(BaseModel):
    take_id: str | None = None


@router.post("/api/takes/{take_id}/review")
async def review_take(take_id: str, body: ReviewIn, request: Request):
    try:
        return await st(request).edit.review_take(take_id, body.verdict, body.notes, OWNER)
    except EditError as exc:
        raise bad(exc) from exc


@router.post("/api/shots/{shot_id}/select-take")
async def select_take(shot_id: str, body: SelectIn, request: Request):
    try:
        return await st(request).edit.select_take(shot_id, body.take_id, OWNER)
    except (EditError, shots.ShotError) as exc:
        raise bad(exc) from exc


# --- the edit ---------------------------------------------------------------------------

class EditPatch(BaseModel):
    clips: dict | None = None
    music: list | None = None
    texts: list | None = None
    settings: dict | None = None
    note: str = ""


@router.get("/api/projects/{project_id}/edit")
async def get_edit(project_id: str, request: Request):
    s = st(request)
    await live_project(s, project_id)
    tl = await s.edit.timeline(project_id)
    return tl | {"suggestions": await s.edit.suggestions(project_id), "revisions": (await s.edit.revisions(project_id))[:20]}


@router.patch("/api/projects/{project_id}/edit")
async def patch_edit(project_id: str, body: EditPatch, request: Request):
    s = st(request)
    await live_project(s, project_id)
    try:
        return await s.edit.update(project_id, body.model_dump(exclude_none=True, exclude={"note"}), OWNER, body.note)
    except EditError as exc:
        raise bad(exc) from exc


@router.post("/api/edit-revisions/{revision_id}/restore")
async def restore_edit(revision_id: str, request: Request):
    try:
        return await st(request).edit.restore(revision_id, OWNER)
    except EditError as exc:
        raise bad(exc) from exc


@router.post("/api/edit-suggestions/{suggestion_id}/{decision}")
async def decide_suggestion(suggestion_id: str, decision: str, request: Request):
    if decision not in ("accept", "reject"):
        raise HTTPException(404, "accept or reject")
    try:
        return await st(request).edit.decide(suggestion_id, decision == "accept", OWNER)
    except EditError as exc:
        raise bad(exc) from exc


# --- exports and deliveries -----------------------------------------------------------

class ExportIn(BaseModel):
    kind: str = "final"
    burn_captions: bool = True
    srt: bool = False
    loudness: bool | None = None
    # Final export quality (backend/app/export.py: CODECS, QUALITY, SIZES, AUDIO_KBPS); drafts ignore them.
    codec: str | None = None
    quality: str | None = None
    size: str | None = None
    ten_bit: bool | None = None
    sharpen: bool | None = None
    audio_kbps: int | None = None


@router.get("/api/projects/{project_id}/exports")
async def list_exports(project_id: str, request: Request):
    s = st(request)
    await live_project(s, project_id)
    return await s.exporter.for_project(project_id)


@router.post("/api/projects/{project_id}/exports", status_code=201)
async def start_export(project_id: str, body: ExportIn, request: Request):
    s = st(request)
    await live_project(s, project_id)
    opts = body.model_dump(exclude_none=True, exclude={"kind"})
    try:
        return await s.exporter.start(project_id, body.kind, opts, OWNER)
    except (ExportError, EditError, LibraryError) as exc:
        raise bad(exc) from exc


@router.post("/api/exports/{export_id}/{action}")
async def export_action(export_id: str, action: str, request: Request):
    s = st(request)
    fn = {"cancel": s.exporter.cancel, "verify": s.exporter.verify}.get(action)
    if not fn:
        raise HTTPException(404, "unknown action")
    try:
        return await fn(export_id, OWNER)
    except ExportError as exc:
        raise bad(exc) from exc


@router.delete("/api/exports/{export_id}")
async def delete_export(export_id: str, request: Request):
    try:
        return await st(request).exporter.delete(export_id, OWNER)
    except ExportError as exc:
        raise bad(exc) from exc


@router.get("/api/exports/{export_id}/file/{what}")
async def export_file(export_id: str, what: str, request: Request):
    try:
        path, name = await st(request).exporter.file(export_id, what)
    except ExportError as exc:
        raise HTTPException(404, str(exc)) from exc
    types = {"mp4": "video/mp4", "srt": "application/x-subrip", "json": "application/json"}
    return FileResponse(path, media_type=types.get(name.rsplit(".", 1)[1], "application/octet-stream"), filename=name)


# --- waveforms and filmstrips -------------------------------------------------------------

@router.get("/api/media/{media_id}/waveform")
async def media_waveform(media_id: str, request: Request):
    s = st(request)
    row = await s.db.fetchone("SELECT * FROM media WHERE id = ? AND kind = 'video' AND has_audio = 1", (media_id,))
    if not row:
        raise HTTPException(404, "no audio")
    return FileResponse(await waveform(s.settings.media_dir / row["rel_path"], row["sha256"], s.settings.media_dir / "cache"),
                        media_type="image/png")


@router.get("/api/media/{media_id}/filmstrip")
async def media_filmstrip(media_id: str, request: Request):
    s = st(request)
    row = await s.db.fetchone("SELECT * FROM media WHERE id = ? AND kind = 'video'", (media_id,))
    if not row:
        raise HTTPException(404, "no such video")
    return FileResponse(await filmstrip(s.settings.media_dir / row["rel_path"], row["sha256"],
                                        s.settings.media_dir / "cache", row["frames"], row["fps"]), media_type="image/jpeg")


@router.get("/api/library/files/{file_id}/waveform")
async def library_waveform(file_id: str, request: Request):
    s = st(request)
    try:
        f = await s.library.file(file_id)
    except LibraryError as exc:
        raise HTTPException(404, str(exc)) from exc
    if f["kind"] != "audio":
        raise HTTPException(404, "not audio")
    return FileResponse(await waveform(s.library.path(f), f["sha256"], s.settings.media_dir / "cache"), media_type="image/png")
