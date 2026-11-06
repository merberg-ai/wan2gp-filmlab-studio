"""Cockpit routes for the Creator (app/creator.py)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth
from .creator import CreatorError
from .library import LibraryError
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


class PlanIn(BaseModel):
    plan: dict


class GenerateIn(BaseModel):
    part: str
    count: int | None = Field(default=None, ge=1, le=4)


class RedraftIn(BaseModel):
    voice: bool = True


@router.get("/api/creations")
async def list_creations(request: Request):
    return await st(request).creator.active()


@router.post("/api/creations", status_code=201)
async def start(body: StartIn, request: Request):
    try:
        return await st(request).creator.start(body.kind, body.brief, OWNER, body.image_template, body.edit_template,
                                               body.voice)
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
        return await s.creator.generate(creation_id, body.part, OWNER, body.count)
    except (CreatorError, LibraryError, RenderError) as exc:
        raise bad(exc) from exc


@router.post("/api/creations/{creation_id}/approve")
async def approve(creation_id: str, request: Request):
    try:
        return await st(request).creator.approve(creation_id, OWNER)
    except (CreatorError, LibraryError) as exc:
        raise bad(exc) from exc


@router.post("/api/creations/{creation_id}/discard")
async def discard(creation_id: str, request: Request):
    try:
        return await st(request).creator.discard(creation_id, OWNER)
    except (CreatorError, TrashError) as exc:
        raise bad(exc) from exc
