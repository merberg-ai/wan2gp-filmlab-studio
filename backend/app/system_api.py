"""F7 routes: backups, health and storage, project templates, the contact sheet. All owner-only."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, shots
from . import library_context
from . import templates as project_templates
from .backups import BackupError
from .db import now
from .health import HealthError
from .director.chat import ChatBusy
from .director.settings import DirectorSettingsError
from .director.tools import TOOL_NAMES
from .presets import PresetError
from .renders import RenderError
from .wangp.client import WanGPError

router = APIRouter(dependencies=[Depends(auth.require_owner)])
OWNER = "owner"


def st(request: Request):
    return request.app.state.studio


# --- backups ----------------------------------------------------------------------------

class BackupIn(BaseModel):
    note: str = Field(default="", max_length=200)


class BackupSettingsIn(BaseModel):
    daily: bool | None = None
    keep: int | None = None
    copy_dir: str | None = Field(default=None, max_length=400)


@router.get("/api/backups")
async def list_backups(request: Request):
    s = st(request)
    return {"backups": await s.backups.list(), "settings": await s.backups.settings(), "dir": str(s.backups.root)}


@router.post("/api/backups", status_code=201)
async def create_backup(body: BackupIn, request: Request):
    try:
        return await st(request).backups.create("manual", body.note, OWNER)
    except BackupError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/api/backups/settings")
async def backup_settings(body: BackupSettingsIn, request: Request):
    try:
        return await st(request).backups.set_settings(body.model_dump(exclude_none=True))
    except (BackupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/backups/{name}/verify")
async def verify_backup(name: str, request: Request, deep: bool = False):
    try:
        return await st(request).backups.verify(name, deep)
    except BackupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/backups/{name}/drill")
async def drill_backup(name: str, request: Request):
    try:
        return await st(request).backups.drill(name)
    except BackupError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/api/backups/{name}")
async def delete_backup(name: str, request: Request):
    try:
        return await st(request).backups.delete(name, OWNER)
    except BackupError as exc:
        raise HTTPException(404, str(exc)) from exc


# --- health -------------------------------------------------------------------------------

class HealthSettingsIn(BaseModel):
    warn_free_gb: int | None = None
    min_free_gb: int | None = None
    gpu_share: str | None = None


class OrphansIn(BaseModel):
    files: list[str]


@router.get("/api/system/health")
async def health_report(request: Request):
    return await st(request).health.report()


@router.put("/api/system/settings")
async def health_settings(body: HealthSettingsIn, request: Request):
    try:
        return await st(request).health.set_settings(body.model_dump(exclude_none=True))
    except (HealthError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/system/clear-caches")
async def clear_caches(request: Request):
    return await st(request).health.clear_caches(OWNER)


@router.post("/api/system/remove-deleted-deliveries")
async def remove_deleted_deliveries(request: Request):
    return await st(request).health.remove_deleted_deliveries(OWNER)


@router.post("/api/system/remove-orphans")
async def remove_orphans(body: OrphansIn, request: Request):
    return await st(request).health.remove_orphans(body.files, OWNER)


# --- templates and the contact sheet -------------------------------------------------------

@router.get("/api/project-templates")
async def list_templates():
    return project_templates.listing()


@router.get("/api/projects/{project_id}/contact-sheet")
async def contact_sheet(project_id: str, request: Request):
    s = st(request)
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")
    return await project_templates.contact_sheet(s.db, s.storyboard, s.edit, project_id)


# --- model presets ------------------------------------------------------------------------

class PresetIn(BaseModel):
    name: str = Field(max_length=60)
    template: str = Field(max_length=200)
    resolution: str = Field(max_length=20)
    frames: int
    use: str = Field(default="", max_length=300)
    tips: str = Field(default="", max_length=600)


class ApplyPresetIn(BaseModel):
    preset_id: str = Field(max_length=120)
    keep_length: bool = False


def _preset_error(exc: Exception) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get("/api/presets")
async def list_presets(request: Request):
    try:
        return await st(request).presets.listing()
    except WanGPError as exc:
        raise HTTPException(502, f"WanGP's templates aren't readable: {exc}") from exc


@router.post("/api/presets", status_code=201)
async def create_preset(body: PresetIn, request: Request):
    try:
        return await st(request).presets.create(body.model_dump(), OWNER)
    except (PresetError, ValueError) as exc:
        raise _preset_error(exc) from exc


@router.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str, request: Request):
    try:
        return await st(request).presets.delete(preset_id, OWNER)
    except PresetError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/shots/{shot_id}/preset")
async def apply_preset(shot_id: str, body: ApplyPresetIn, request: Request):
    s = st(request)
    try:
        shot = await shots.get_shot(s.db, shot_id)
        return await s.presets.apply(shot, body.preset_id, OWNER, body.keep_length)
    except (PresetError, shots.ShotError, RenderError) as exc:
        raise _preset_error(exc) from exc


# --- context-aware library search -----------------------------------------------------------

@router.get("/api/shots/{shot_id}/library-suggestions")
async def shot_library_suggestions(shot_id: str, request: Request, q: str = "", kind: str | None = None):
    s = st(request)
    shot = await s.db.fetchone("SELECT project_id FROM shots WHERE id = ? AND deleted_at IS NULL", (shot_id,))
    if not shot:
        raise HTTPException(404, "no such shot")
    return await library_context.suggest(s.db, s.library, shot["project_id"], shot_id=shot_id, query=q, kind=kind)


@router.get("/api/projects/{project_id}/library-suggestions")
async def project_library_suggestions(project_id: str, request: Request, q: str = "", kind: str | None = None,
                                      scene_id: str | None = None):
    s = st(request)
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")
    return await library_context.suggest(s.db, s.library, project_id, scene_id=scene_id, query=q, kind=kind)


# --- LAN self-test (F8) -------------------------------------------------------------------
# The cockpit's #/selftest page runs its checks in the browser and posts the outcome here,
# so each device's result is kept with the address it came from.

SELFTEST_KV, SELFTEST_KEEP = "selftest.results", 50


class SelfTestCheck(BaseModel):
    name: str = Field(max_length=60)
    ok: bool
    detail: str = Field(default="", max_length=300)
    ms: float | None = None


class SelfTestIn(BaseModel):
    checks: list[SelfTestCheck] = Field(max_length=20)
    user_agent: str = Field(default="", max_length=300)
    screen: str = Field(default="", max_length=40)
    origin: str = Field(default="", max_length=120)


@router.get("/api/selftest")
async def selftest_info(request: Request):
    """What the page needs: the address the backend sees and a video to test streaming with."""
    s = st(request)
    video = await s.db.fetchone("SELECT id, bytes FROM media WHERE kind = 'video' ORDER BY created_at DESC LIMIT 1")
    return {"client": request.client.host if request.client else None, "sample_video": video and video["id"],
            "results": list(reversed(await s.db.kv_get(SELFTEST_KV, []) or []))}


@router.post("/api/selftest", status_code=201)
async def selftest_record(body: SelfTestIn, request: Request):
    s = st(request)
    rec = {"at": now(), "client": request.client.host if request.client else None,
           "passed": all(c.ok for c in body.checks) and bool(body.checks)} | body.model_dump()
    kept = (await s.db.kv_get(SELFTEST_KV, []) or [])[-(SELFTEST_KEEP - 1):] + [rec]
    await s.db.kv_set(SELFTEST_KV, kept)
    await s.db.audit(OWNER, "selftest.run", rec["client"] or "?",
                     {"passed": rec["passed"], "failed": [c.name for c in body.checks if not c.ok]})
    return rec


# --- the director (Settings → Director) ----------------------------------------------------

class DirectorSettingsIn(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)
    reply_style: str | None = None
    chat_rounds: int | None = None
    plan_rounds: int | None = None
    default_autonomy: str | None = None
    model: str | None = Field(default=None, max_length=120)


class DirectorTestIn(BaseModel):
    model: str | None = Field(default=None, max_length=120)  # try this one; omitted: the saved choice


@router.get("/api/director/models")
async def director_models(request: Request):
    return await st(request).director_settings.models()


@router.get("/api/director")
async def director_overview(request: Request):
    """Settings, what the director runs on, and its installed instructions (read-only)."""
    s = st(request)
    ds = s.director_settings
    return {"settings": await ds.get(), "connected": await s.gateway.health(), "agent": ds.agent,
            **ds.model(), "instructions": ds.instructions(), "tools": sorted(TOOL_NAMES)}


@router.put("/api/director/settings")
async def director_settings(body: DirectorSettingsIn, request: Request):
    try:
        return await st(request).director_settings.update(body.model_dump(exclude_none=True), OWNER)
    except DirectorSettingsError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/director/test")
async def director_test(request: Request, body: DirectorTestIn | None = None):
    return await st(request).director_settings.test(OWNER, body.model if body else None)


@router.post("/api/projects/{project_id}/chat/fresh")
async def director_fresh(project_id: str, request: Request):
    s = st(request)
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")
    try:
        return await s.chat.fresh(project_id, OWNER)
    except ChatBusy as exc:
        raise HTTPException(409, str(exc)) from exc
