"""Storybook routes: the book, its frames, the director's story text, and exports (backend/app/storybook.py)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import auth
from .director.chat import ChatBusy
from .storybook import StorybookError
from .storybook_writer import WriterError

router = APIRouter(dependencies=[Depends(auth.require_owner)])
OWNER = "owner"


def st(request: Request):
    return request.app.state.studio


async def live_project(s, project_id: str):
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")


class BookPatch(BaseModel):
    layout: str | None = None
    per_page: int | None = None
    chapters: bool | None = None
    cover: dict | None = None
    panels: dict | None = None
    note: str = ""


@router.get("/api/projects/{project_id}/storybook")
async def get_book(project_id: str, request: Request):
    s = st(request)
    await live_project(s, project_id)
    return await s.storybook.view(project_id) | {"writer": s.story_writer.plan(project_id),
                                                 "exports": await s.storybook.exports(project_id)}


@router.patch("/api/projects/{project_id}/storybook")
async def patch_book(project_id: str, body: BookPatch, request: Request):
    s = st(request)
    await live_project(s, project_id)
    try:
        return await s.storybook.update(project_id, body.model_dump(exclude_none=True, exclude={"note"}), OWNER,
                                        body.note or "edited the book")
    except (StorybookError, ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/projects/{project_id}/storybook/revisions/{revision_id}/restore")
async def restore_book(project_id: str, revision_id: str, request: Request):
    s = st(request)
    await live_project(s, project_id)
    try:
        return await s.storybook.restore(project_id, revision_id, OWNER)
    except StorybookError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/storybook/frame/{take_id}")
async def book_frame(take_id: str, request: Request, t: float = 0, crop: str = "", size: str = "small"):
    """A frame of a take (crop: x,y,w,h as fractions), for the book's preview and the frame picker."""
    c = None
    if crop:
        try:
            x, y, w, h = (float(v) for v in crop.split(","))
        except ValueError:
            raise HTTPException(422, "crop is x,y,w,h") from None
        c = {"x": x, "y": y, "w": w, "h": h}
    try:
        path = await st(request).storybook.frame(take_id, t, c, "full" if size == "full" else "small")
    except StorybookError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, f"couldn't grab the frame: {exc}") from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# --- ✨ Write the story ----------------------------------------------------------------------

class WriteIn(BaseModel):
    note: str = ""


class WriteApplyIn(BaseModel):
    texts: dict[str, str]
    title: str | None = None
    subtitle: str | None = None


@router.post("/api/projects/{project_id}/storybook/write", status_code=202)
async def write_story(project_id: str, body: WriteIn, request: Request):
    s = st(request)
    await live_project(s, project_id)
    if project_id in s.enhance_all.runs:
        raise HTTPException(409, "the director is enhancing the storyboard's shots; wait for it or stop it")
    try:
        return {"plan": await s.story_writer.start(project_id, body.note[:500])}
    except ChatBusy as exc:
        raise HTTPException(409, f"{exc}; wait for it to finish or stop it") from exc
    except WriterError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/api/projects/{project_id}/storybook/write")
async def discard_story(project_id: str, request: Request):
    s = st(request)
    await live_project(s, project_id)
    return {"discarded": await s.story_writer.discard(project_id)}


@router.post("/api/projects/{project_id}/storybook/write/apply")
async def apply_story(project_id: str, body: WriteApplyIn, request: Request):
    s = st(request)
    await live_project(s, project_id)
    try:
        return await s.story_writer.apply(project_id, body.texts, body.title, body.subtitle, OWNER)
    except (StorybookError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


# --- exports ----------------------------------------------------------------------------------

@router.post("/api/projects/{project_id}/storybook/exports", status_code=201)
async def export_book(project_id: str, request: Request):
    s = st(request)
    await live_project(s, project_id)
    try:
        return await s.storybook.start_export(project_id, OWNER)
    except StorybookError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/storybook-exports/{export_id}/file/{what}")
async def export_file(export_id: str, what: str, request: Request, download: bool = False):
    s = st(request)
    try:
        row = await s.storybook.export_row(export_id)
        path, mime, name = s.storybook.export_file(row, what)
    except StorybookError as exc:
        raise HTTPException(404, str(exc)) from exc
    title = (await s.db.fetchone("SELECT title FROM projects WHERE id = ?", (row["project_id"],)) or {}).get("title") or "storybook"
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in title).strip()[:60] or "storybook"
    fname = f"{safe} - {name}" if what in ("pdf", "zip", "cbz") else name
    return FileResponse(path, media_type=mime, filename=fname if download else None,
                        content_disposition_type="attachment" if download else "inline")


@router.delete("/api/storybook-exports/{export_id}")
async def delete_book_export(export_id: str, request: Request):
    try:
        return await st(request).storybook.delete_export(export_id, OWNER)
    except StorybookError as exc:
        raise HTTPException(404, str(exc)) from exc
