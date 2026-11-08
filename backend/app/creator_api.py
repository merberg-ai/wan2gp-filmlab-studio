"""Cockpit routes for the Creator (app/creator.py)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import tempfile
from pathlib import Path

from fastapi.responses import FileResponse

from . import auth
from .creator import CreatorError
from .library import LibraryError
from .library_api import _body
from .shots import ShotError
from .renders import RenderError
from .trash import TrashError

router = APIRouter(dependencies=[Depends(auth.require_owner)])
OWNER = "owner"


def st(request: Request):
    return request.app.state.studio


def bad(exc: Exception) -> HTTPException:
    return HTTPException(422 if isinstance(exc, (CreatorError, LibraryError)) else 409, str(exc))


class StartIn(BaseModel):
    kind: str
    brief: str = Field(min_length=1, max_length=4000)
    image_template: str | None = None
    edit_template: str | None = None
    voice: bool = True
    resolution: str | None = None
    voice_mode: str | None = None    # design | clone
    sound_type: str | None = None    # music | ambience


class SettingsIn(BaseModel):
    resolution: str | None = None
    voice_mode: str | None = None
    image_template: str | None = None
    edit_template: str | None = None


class ApproveIn(BaseModel):
    project_id: str | None = None    # also into this project's cast
    shot_id: str | None = None       # also onto this shot as its next reference


class PlanIn(BaseModel):
    plan: dict


class GenerateIn(BaseModel):
    part: str
    count: int | None = Field(default=None, ge=1, le=4)
    index: int | None = Field(default=None, ge=0, le=3)  # part "view": which one
    line: str | None = None                              # part "say": the words


class RedraftIn(BaseModel):
    voice: bool = True


@router.get("/api/creations")
async def list_creations(request: Request):
    return await st(request).creator.active()


@router.post("/api/creations", status_code=201)
async def start(body: StartIn, request: Request):
    try:
        return await st(request).creator.start(body.kind, body.brief, OWNER, body.image_template, body.edit_template,
                                               body.voice, body.resolution, body.voice_mode, body.sound_type)
    except (CreatorError, LibraryError) as exc:
        raise bad(exc) from exc


@router.get("/api/creations/{creation_id}")
async def get_creation(creation_id: str, request: Request):
    try:
        return await st(request).creator.get(creation_id)
    except CreatorError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/api/creations/{creation_id}/plan")
async def update_plan(creation_id: str, body: PlanIn, request: Request):
    try:
        return await st(request).creator.update_plan(creation_id, body.plan, OWNER)
    except (CreatorError, LibraryError) as exc:
        raise bad(exc) from exc


@router.post("/api/creations/{creation_id}/redraft")
async def redraft(creation_id: str, body: RedraftIn, request: Request):
    try:
        return await st(request).creator.redraft(creation_id, OWNER, body.voice)
    except CreatorError as exc:
        raise bad(exc) from exc


@router.post("/api/creations/{creation_id}/generate", status_code=201)
async def generate(creation_id: str, body: GenerateIn, request: Request):
    s = st(request)
    if not s.wangp_ready.is_set():
        raise HTTPException(503, "WanGP is not connected; start WanGP with MCP enabled")
    try:
        return await s.creator.generate(creation_id, body.part, OWNER, body.count, body.index, body.line)
    except (CreatorError, LibraryError, RenderError) as exc:
        raise bad(exc) from exc


@router.post("/api/creations/{creation_id}/approve")
async def approve(creation_id: str, request: Request, body: ApproveIn | None = None):
    body = body or ApproveIn()
    try:
        return await st(request).creator.approve(creation_id, OWNER, body.project_id, body.shot_id)
    except (CreatorError, LibraryError, ShotError) as exc:
        raise bad(exc) from exc


@router.patch("/api/creations/{creation_id}/settings")
async def creation_settings(creation_id: str, body: SettingsIn, request: Request):
    try:
        return await st(request).creator.update_settings(creation_id, body.model_dump(exclude_unset=True), OWNER)
    except (CreatorError, LibraryError) as exc:
        raise bad(exc) from exc


@router.put("/api/creations/{creation_id}/voice-sample")
async def voice_sample(creation_id: str, request: Request):
    """Raw-body upload of the recording a character's voice is cloned from."""
    data = await _body(request)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample"
        path.write_bytes(data)
        try:
            return await st(request).creator.set_voice_sample(creation_id, path, OWNER)
        except (CreatorError, LibraryError) as exc:
            raise bad(exc) from exc


@router.get("/api/creations/{creation_id}/clips/{clip_id}")
async def clip(creation_id: str, clip_id: str, request: Request, thumb: bool = False):
    try:
        path = await st(request).creator.clip_path(creation_id, clip_id, thumb)
    except CreatorError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="image/jpeg" if thumb else "video/mp4")


@router.post("/api/creations/{creation_id}/discard")
async def discard(creation_id: str, request: Request):
    try:
        return await st(request).creator.discard(creation_id, OWNER)
    except (CreatorError, TrashError) as exc:
        raise bad(exc) from exc
