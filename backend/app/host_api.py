"""Settings > Startup & WanGP: the Windows helper's status and the requests the cockpit sends it (host.py)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import auth
from .host import HostError

router = APIRouter(dependencies=[Depends(auth.require_owner)])


def st(request: Request):
    return request.app.state.studio


class HostSettings(BaseModel):
    wangp_autostart: bool | None = None
    wangp_restart: bool | None = None
    start_at_logon: bool | None = None
    wangp_path: str | None = None  # "" = find it by itself again


def _send(s, action: str, **data) -> dict:
    try:
        name = s.host.request(action, **data)
    except HostError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"requested": action, "id": name}


@router.get("/api/host")
async def host_status(request: Request):
    s = st(request)
    return s.host.status() | {"wangp_connected": s.wangp_ready.is_set()}


@router.post("/api/host/wangp/start", status_code=202)
async def start_wangp(request: Request):
    s = st(request)
    await s.db.audit("owner", "host.start_wangp", None)
    return _send(s, "start-wangp")


@router.post("/api/host/detect", status_code=202)
async def detect(request: Request):
    return _send(st(request), "detect")


@router.patch("/api/host/settings", status_code=202)
async def host_settings(body: HostSettings, request: Request):
    s = st(request)
    data = body.model_dump(exclude_unset=True)
    await s.db.audit("owner", "host.settings", None, data)
    return _send(s, "settings", **data)
