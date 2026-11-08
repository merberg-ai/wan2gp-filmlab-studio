"""The Console's lines (console.py), newest first, with filters; one line's details."""

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth

router = APIRouter(dependencies=[Depends(auth.require_owner)])


@router.get("/api/console")
async def console(request: Request, before_id: int | None = None, limit: int = 200, level: str | None = None,
                  source: str | None = None, project_id: str | None = None, q: str | None = None):
    return await request.app.state.studio.console.listing(before_id, limit, level, source, project_id, q)


@router.get("/api/console/{entry_id}")
async def console_entry(entry_id: int, request: Request):
    row = await request.app.state.studio.console.detail(entry_id)
    if not row:
        raise HTTPException(404, "no such console line")
    return row
