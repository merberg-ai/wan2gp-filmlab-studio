"""Music video setup (music_video.py) and WanGP's own web page, for the cockpit."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import auth
from .capabilities import CapabilityError
from .library import LibraryError
from .music_video import MusicVideoError
from .renders import RenderError

router = APIRouter(dependencies=[Depends(auth.require_owner)])


def st(request: Request):
    return request.app.state.studio


async def _guard(call):
    try:
        return await call
    except (MusicVideoError, LibraryError, CapabilityError) as exc:
        raise HTTPException(409 if "no such project" not in str(exc) else 404, str(exc)) from exc
    except RenderError as exc:
        raise HTTPException(409, str(exc)) from exc


class MusicPatch(BaseModel):
    mode: str | None = None
    lyrics: str | None = None
    style: str | None = None
    speech_template: str | None = None
    song_file_id: str | None = None


class SongIn(BaseModel):
    lyrics: str | None = None
    style: str | None = None
    seconds: float | None = None
    template: str | None = None


@router.get("/api/projects/{project_id}/music-video")
async def music_video(project_id: str, request: Request):
    return await _guard(st(request).music.view(project_id))


@router.patch("/api/projects/{project_id}/music-video")
async def update_music_video(project_id: str, body: MusicPatch, request: Request):
    return await _guard(st(request).music.update(project_id, body.model_dump(exclude_unset=True), "owner"))


@router.post("/api/projects/{project_id}/music-video/song", status_code=202)
async def generate_song(project_id: str, body: SongIn, request: Request):
    return await _guard(st(request).music.generate_song(project_id, "owner", body.lyrics, body.style, body.seconds,
                                                        body.template))


@router.post("/api/projects/{project_id}/music-video/song/candidates/{candidate_id}/use")
async def use_song(project_id: str, candidate_id: str, request: Request):
    return await _guard(st(request).music.use_candidate(project_id, candidate_id, "owner"))


@router.post("/api/projects/{project_id}/music-video/song-asset")
async def song_asset(project_id: str, request: Request):
    """The project's song asset, created on first use: the cockpit uploads a track to its look, then picks it."""
    s = st(request)
    asset_id = await _guard(s.music.song_asset(project_id, "owner"))
    asset = await s.library.asset(asset_id)
    return {"asset_id": asset_id, "variant_id": asset["variants"][0]["id"]}


@router.get("/api/wangp/webui")
async def wangp_webui(request: Request):
    """Where WanGP's own web page is (to watch a download there), or {"found": false}."""
    found = await st(request).wangp_ui.locate()
    return {"found": bool(found), **(found or {})}
