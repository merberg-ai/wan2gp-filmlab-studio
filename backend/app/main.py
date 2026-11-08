"""FilmLab Studio backend: FastAPI app, REST API, /ws event stream."""

import asyncio
import contextlib
import ipaddress
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import VERSION, auth, autonomy_api, creator_api, edit_api, library_api, shots, storyboard_api, system_api
from . import templates as project_templates
from .backups import Backups
from .gpu_stats import GpuMonitor
from .health import Health
from .batches import Batches
from .edit import Edit
from .export import Exporter
from .capabilities import Capabilities
from .config import Settings
from .db import Database, new_id, now
from .director.chat import ChatBusy, DirectorChat, failed
from .director.settings import DirectorSettings
from .director.enhance import can_enhance, enhance_brief
from .director.gateway import GatewayClient
from .director.tools import DirectorTools
from .events import EventBus
from .creator import Creator
from .images import ImageJobs
from .inspect_takes import Inspector
from .runs import Runs
from .storyboard import Storyboard, StoryboardError
from .trash import Trash, TrashError
from .library import Library
from .presets import Presets
from .references import References
from .renders import ACTIVE, RenderError, RenderService, UnknownTemplate, summarize_queue
from .wangp import errors
from .wangp.client import WanGPClient

log = logging.getLogger("filmlab")
HEARTBEAT_S = 20
LOGIN_FAIL_DELAY_S = 1


class ClientNetworks:
    """ASGI middleware: refuse HTTP and WebSocket clients outside the allowed networks."""

    def __init__(self, app, nets: tuple[str, ...]):
        self.app = app
        self.nets = [ipaddress.ip_network(n.strip(), strict=False) for n in nets if n.strip()]

    def allowed(self, host: str | None) -> bool:
        try:
            addr = ipaddress.ip_address(host or "")
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return any(addr in net for net in self.nets)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not self.allowed((scope.get("client") or (None,))[0]):
            if scope["type"] == "http":
                return await JSONResponse({"detail": "client network not allowed"}, 403)(scope, receive, send)
            return await send({"type": "websocket.close", "code": 4403})
        return await self.app(scope, receive, send)


class LoginThrottle:
    """Per-client-IP failed-login counter; locks a client out once it hits the limit within the window."""

    def __init__(self, max_failures: int, window_s: int):
        self.max, self.window = max_failures, window_s
        self.failures: dict[str, deque] = defaultdict(deque)

    def _recent(self, ip: str) -> deque:
        q, cutoff = self.failures[ip], time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def locked(self, ip: str) -> bool:
        return len(self._recent(ip)) >= self.max

    def fail(self, ip: str):
        self._recent(ip).append(time.monotonic())

    def clear(self, ip: str):
        self.failures.pop(ip, None)


class Studio:
    """Everything the routes need, built once per process."""

    def __init__(self, settings: Settings, wangp: WanGPClient | None = None, gateway: GatewayClient | None = None,
                 vision_transport=None, gpu_query=None):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.bus = EventBus(self.db)
        self.wangp = wangp or WanGPClient(settings.wangp_url)
        self.renders = RenderService(self.db, self.bus, self.wangp, settings.media_dir, settings.wangp_workspace)
        self.library = Library(self.db, self.bus, settings.media_dir)
        self.refs = References(self.db, self.wangp, self.library, self.renders)
        self.caps = Capabilities(self.db, self.wangp, settings.data_dir, self.refs.wangp_id_for_path)
        self.refs.caps = self.caps
        self.renders.prepare_shot = self.refs.prepare_shot
        self.renders.reupload_refs = self.refs.reupload
        self.renders.reupload_voices = self.refs.reupload_voices
        self.images = ImageJobs(self.renders, self.library, self.refs, self.caps)
        self.trash = Trash(self.db, self.bus, self.library, self.refs, settings.media_dir)
        self.storyboard = Storyboard(self.db, self.bus, self.library, self.caps, self.renders)
        self.batches = Batches(self.db, self.bus, self.renders, self.storyboard)
        self.edit = Edit(self.db, self.bus, self.storyboard, self.library, settings.media_dir)
        self.exporter = Exporter(self.db, self.bus, self.edit, self.library, settings.media_dir)
        self.inspector = Inspector(self.db, self.bus, settings.media_dir, transport=vision_transport)
        self.inspector.library = self.library
        self.backups = Backups(self.db, self.bus, settings.db_path, settings.media_dir, settings.backup_dir)
        self.backups.exporter = self.exporter
        self.presets = Presets(self.db, self.caps, self.renders)
        self.health = Health(self.db, settings.media_dir, settings.data_dir, settings.backup_dir,
                             ollama_transport=vision_transport)
        self.gateway = gateway or GatewayClient(settings.gateway_url, settings.gateway_token, settings.director_agent)
        self.tools = DirectorTools(self)
        self.director_settings = DirectorSettings(self.db, settings, self.gateway, ollama_transport=vision_transport)
        self.chat = DirectorChat(self.db, self.bus, self.gateway, self.tools, settings.director_max_tool_rounds,
                                 self.director_settings)
        self.runs = Runs(self.db, self.bus, self.renders, self.storyboard, self.edit, self.inspector, self.chat.send)
        self.creator = Creator(self)
        self.runs.held = self.gpu_held
        self.health.wangp = (self.wangp, None)
        self.renders.gate = self.gpu_held
        self.renders.listeners += [self.runs.job_finished, self._inspect_new_take]
        self.gpu = GpuMonitor(self.bus, **({"query": gpu_query} if gpu_query else {}))
        self.wangp_ready = asyncio.Event()
        self._connector: asyncio.Task | None = None

    async def start(self):
        self.settings.media_dir.mkdir(parents=True, exist_ok=True)
        self._connector = asyncio.create_task(self._connect_wangp())
        await self.exporter.resume_all()  # exports only need ffmpeg, not WanGP
        self.backups.start_daily()
        self.gpu.start()

    async def _connect_wangp(self):
        delay = 2
        while True:
            try:
                await self.renders.connect()
                await self.renders.resume_all()
                await self.images.resume_all()
                self.wangp_ready.set()
                await self.batches.resume_all()
                await self.runs.resume_all()
                return
            except Exception as exc:
                log.warning("WanGP not reachable (%s: %s); retrying in %ss", type(exc).__name__, exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def new_shot_template(self, project_id: str, name: str | None) -> tuple[dict, bool]:
        """(template, explicit) for a new shot: the named one, or the project's/global default without refs."""
        if name:
            return await self.renders.resolve_template(name), True
        project = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        default = project.get("video_plain_template") or (await self.caps.settings())["video_plain"]
        try:
            return await self.renders.resolve_template(default), False
        except UnknownTemplate:  # configured default not installed any more: WanGP's own default
            return await self.renders.resolve_template(None), False

    async def gpu_held(self) -> str | None:
        """The owner's global hold on GPU work (renders, batches, redo runs, image generation)."""
        hold = await self.db.kv_get("gpu.hold")
        if hold and hold.get("on"):
            return f"GPU work is on hold (since {hold['since'][:16].replace('T', ' ')}); release it in the cockpit"
        return await self.health.storage_block() or await self.health.gpu_share_block()

    async def set_gpu_hold(self, on: bool, cancel: bool, actor: str) -> dict:
        await self.db.kv_set("gpu.hold", {"on": bool(on), "since": now(), "by": actor})
        await self.db.audit(actor, "gpu.hold" if on else "gpu.release", None, {"cancel": cancel})
        cancelled = []
        if on:
            for r in await self.db.fetchall("SELECT id FROM director_runs WHERE status IN ('running', 'paused')"):
                await self.runs.stop(r["id"], actor, "stopped: GPU work put on hold", cancel=cancel)
            for b in await self.db.fetchall("SELECT id FROM batches WHERE status = 'running'"):
                await self.batches._set_status(b["id"], "paused", "GPU work is on hold")
                p = await self.db.fetchone("SELECT project_id FROM batches WHERE id = ?", (b["id"],))
                await self.batches._changed(p["project_id"], b["id"])
            if cancel:
                for svc in (self.renders, self.images):
                    for j in await self.db.fetchall(f"SELECT id FROM {svc.TABLE} WHERE status IN {ACTIVE}"):
                        try:
                            await svc.cancel(j["id"], actor)
                            cancelled.append(j["id"])
                        except RenderError as exc:
                            log.info("hold: cancel of %s: %s", j["id"], exc)
        await self.bus.publish("gpu.hold", {"on": bool(on)})
        return {"on": bool(on), "cancelled": cancelled}

    async def _inspect_new_take(self, job: dict, status: str):
        if status == "done":
            take = await self.db.fetchone("SELECT id FROM takes WHERE job_id = ?", (job["id"],))
            if take:
                await self.inspector.take_done(take["id"])

    async def stop(self):
        self.batches.closing = True  # first: a process on its way out submits nothing new
        self.runs.closing = True
        await self.exporter.shutdown()  # an export cut off here restarts cleanly on the next start
        if self._connector:
            self._connector.cancel()
        await self.runs.shutdown()
        await self.backups.shutdown()
        await self.gpu.shutdown()
        await self.inspector.shutdown()
        await self.chat.shutdown()
        await self.renders.shutdown()
        await self.images.shutdown()
        await self.creator.shutdown()
        await self.wangp.aclose()
        await self.gateway.aclose()
        self.db.close()


# --- request models ------------------------------------------------------

class Login(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class ProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    logline: str = Field(default="", max_length=1000)
    template: str | None = Field(default=None, max_length=40)


class ProjectPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    logline: str | None = Field(default=None, max_length=1000)
    default_resolution: str | None = None
    default_frames: int | None = Field(default=None, ge=9, le=481)
    brief: str | None = Field(default=None, max_length=8000)
    target_seconds: int | None = Field(default=None, ge=0, le=600)
    autonomy: str | None = Field(default=None, pattern="^A[0-3]$")
    style: str | None = Field(default=None, max_length=2000)


class RenderIn(BaseModel):
    preview: bool = False  # a quick low-resolution take first (renders.PREVIEW_SCALE)


class ShotIn(BaseModel):
    title: str | None = None
    description: str | None = None
    prompt: str | None = None
    idea: str | None = None
    frames: int | None = None
    resolution: str | None = None
    template: str | None = None
    seed: int | None = None
    transition: str | None = None
    sound: dict | None = None  # who says which exact words, ambience, music (app/sound.py); {} clears it
    scene_id: str | None = None  # on create: the scene to add the shot to


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


# --- app -----------------------------------------------------------------

def create_app(settings: Settings | None = None, studio: Studio | None = None) -> FastAPI:
    settings = settings or Settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.studio = studio or Studio(settings)
        app.state.db = app.state.studio.db
        await app.state.studio.start()
        yield
        await app.state.studio.stop()

    app = FastAPI(title="FilmLab Studio", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(library_api.router)
    app.include_router(creator_api.router)
    app.include_router(storyboard_api.router)
    app.include_router(edit_api.router)
    app.include_router(autonomy_api.router)
    app.include_router(system_api.router)
    owner = Depends(auth.require_owner)

    def st(request: Request) -> Studio:
        return request.app.state.studio

    async def project_or_404(s: Studio, project_id: str) -> dict:
        p = await s.db.fetchone("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
        if not p:
            raise HTTPException(404, "no such project")
        return p

    async def shot_or_404(s: Studio, shot_id: str) -> dict:
        try:
            return await shots.get_shot(s.db, shot_id)
        except shots.ShotError:
            raise HTTPException(404, "no such shot") from None

    # auth ------------------------------------------------------------------

    throttle = LoginThrottle(settings.login_max_failures, settings.login_window_s)

    @app.post("/api/auth/login")
    async def login(body: Login, request: Request, response: Response):
        s = st(request)
        ip = request.client.host if request.client else "?"
        if throttle.locked(ip):
            await s.db.audit("anonymous", "auth.login_throttled", ip)
            raise HTTPException(429, "too many failed logins; try again later")
        if not await auth.verify_owner(s.db, body.username, body.password):
            throttle.fail(ip)
            await s.db.audit("anonymous", "auth.login_failed", f"{body.username[:64]} from {ip}")
            await asyncio.sleep(LOGIN_FAIL_DELAY_S)
            raise HTTPException(401, "wrong username or password")
        throttle.clear(ip)
        token, csrf = await auth.create_session(s.db, settings.session_hours)
        response.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict", secure=settings.secure_cookies,
                            max_age=settings.session_hours * 3600, path="/")
        await s.db.audit("owner", "auth.login")
        return {"csrf_token": csrf}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response, _=owner):
        await auth.end_session(st(request).db, request.cookies.get(auth.COOKIE))
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me(sess=owner):
        return {"csrf_token": sess["csrf_token"]}

    # health ----------------------------------------------------------------

    @app.get("/api/health")
    async def health(request: Request, _=owner):
        s = st(request)
        wangp = {"connected": s.wangp_ready.is_set()}
        if s.wangp_ready.is_set():
            try:
                q = summarize_queue(await s.wangp.session("list_queue"), s.wangp.agent_context)
                wangp |= {"queue_total": q["total"], "queue_running": q["running"], "queue_filmlab": q["filmlab"],
                          "queue_other": q["other"], "workspace": (s.renders.workspace or {}).get("name")}
            except Exception as exc:
                wangp |= {"connected": False, "error": str(exc)[:200]}
        return {"version": VERSION, "wangp": wangp,
                "director": {"connected": await s.gateway.health(), "agent": settings.director_agent},
                "last_event_id": await s.bus.last_event_id()}

    # projects --------------------------------------------------------------

    @app.get("/api/projects")
    async def list_projects(request: Request, _=owner):
        return await st(request).db.fetchall(
            "SELECT p.*, (SELECT COUNT(*) FROM shots s WHERE s.project_id = p.id AND s.deleted_at IS NULL) AS shot_count "
            "FROM projects p WHERE p.deleted_at IS NULL ORDER BY updated_at DESC")

    @app.post("/api/projects", status_code=201)
    async def create_project(body: ProjectIn, request: Request, _=owner):
        s = st(request)
        pid, ts = new_id(), now()
        try:
            extra = project_templates.apply(body.template)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        cols = {"id": pid, "title": body.title.strip(), "logline": body.logline.strip(), "created_at": ts,
                "updated_at": ts, "autonomy": (await s.director_settings.get())["default_autonomy"]} | extra
        await s.db.execute(f"INSERT INTO projects ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           tuple(cols.values()))
        await s.db.audit("owner", "project.create", pid, {"template": body.template} if body.template else None)
        await s.bus.publish("project.updated", {"project_id": pid, "created": True}, pid)
        return await project_or_404(s, pid)

    @app.patch("/api/projects/{project_id}")
    async def update_project(project_id: str, body: ProjectPatch, request: Request, _=owner):
        s = st(request)
        await project_or_404(s, project_id)
        data = body.model_dump(exclude_none=True)
        if "title" in data:
            data["title"] = data["title"].strip()
            if not data["title"]:
                raise HTTPException(422, "title can't be empty")
        if "logline" in data:
            data["logline"] = data["logline"].strip()
        if "brief" in data:
            data["brief"] = data["brief"].strip()
        if data.get("target_seconds") == 0:
            data["target_seconds"] = None  # 0 clears the target
        if "default_resolution" in data or "default_frames" in data:
            try:  # same rules as a shot's size and length
                checked = shots.clean_fields({"resolution": data.get("default_resolution"),
                                              "frames": data.get("default_frames")}, partial=True)
            except shots.ShotError as exc:
                raise HTTPException(422, str(exc)) from exc
            data |= {k: v for k, v in (("default_resolution", checked.get("resolution")),
                                       ("default_frames", checked.get("frames"))) if v is not None}
        if "target_seconds" in body.model_fields_set and body.target_seconds in (None, 0):
            data["target_seconds"] = None
        if data:
            sets = ", ".join(f"{k} = ?" for k in data)
            await s.db.execute(f"UPDATE projects SET {sets}, updated_at = ? WHERE id = ?", (*data.values(), now(), project_id))
            await s.db.audit("owner", "project.update", project_id, sorted(data))
            await s.bus.publish("project.updated", {"project_id": project_id}, project_id)
        return await project_or_404(s, project_id)

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str, request: Request, _=owner):
        try:
            return await st(request).trash.delete_project(project_id, "owner")
        except TrashError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/shots/{shot_id}")
    async def delete_shot(shot_id: str, request: Request, _=owner):
        s = st(request)
        await shot_or_404(s, shot_id)
        try:
            return await s.trash.delete_shot(shot_id, "owner")
        except TrashError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/takes/{take_id}")
    async def delete_take(take_id: str, request: Request, _=owner):
        try:
            return await st(request).trash.delete_take(take_id, "owner")
        except TrashError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/trash")
    async def trash_list(request: Request, _=owner):
        return await st(request).trash.listing()

    @app.post("/api/trash/{kind}/{row_id}/restore")
    async def trash_restore(kind: str, row_id: str, request: Request, _=owner):
        try:
            return await st(request).trash.restore(kind, row_id, "owner")
        except TrashError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/trash/empty")
    async def trash_empty(request: Request, _=owner):
        return await st(request).trash.empty("owner")

    @app.delete("/api/trash/{kind}/{row_id}")
    async def trash_purge(kind: str, row_id: str, request: Request, _=owner):
        try:
            return await st(request).trash.purge(kind, row_id, "owner")
        except TrashError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str, request: Request, _=owner):
        s = st(request)
        project = await project_or_404(s, project_id)
        # One consistent read: a render finishing mid-request must not show a done job on a rendering shot.
        shot_rows, jobs, takes, approvals = await s.db.fetchall_many(
            ("SELECT * FROM shots WHERE project_id = ? AND deleted_at IS NULL ORDER BY position", (project_id,)),
            ("SELECT id, shot_id, status, phase, current_step, total_steps, recovered, error, error_code, queue_ahead, "
             "queue_running, stalled, submitted_at, started_at, finished_at, preview FROM render_jobs WHERE project_id = ? "
             "AND shot_id IN (SELECT id FROM shots WHERE deleted_at IS NULL) "  # a deleted shot's renders go with it
             "ORDER BY submitted_at", (project_id,)),
            ("SELECT t.id, t.shot_id, t.job_id, t.media_id, t.thumb_media_id, t.provenance, t.created_at, t.verdict, "
             "t.preview, "
             "t.notes, m.width, "
             "m.height, m.fps, m.frames, m.duration_s, m.has_audio, m.bytes FROM takes t JOIN media m ON m.id = t.media_id "
             "JOIN shots s ON s.id = t.shot_id WHERE s.project_id = ? AND s.deleted_at IS NULL AND t.deleted_at IS NULL "
             "ORDER BY t.rowid",
             (project_id,)),
            ("SELECT * FROM approvals WHERE project_id = ? AND status = 'pending' ORDER BY created_at", (project_id,)))
        for j in jobs:
            j["error_info"], j["stalled"], j["preview"] = errors.describe(j.pop("error_code")), bool(j["stalled"]), bool(j["preview"])
        for t in takes:
            t["provenance"] = json.loads(t["provenance"]) if t["provenance"] else None
            t["preview"] = bool(t["preview"])
        refs = await s.db.fetchall(
            "SELECT r.shot_id, r.position, r.role, r.variant_id, r.file_id, v.name AS variant_name, v.version, "
            "v.locked, a.id AS asset_id, a.name AS asset_name, a.kind FROM shot_refs r JOIN shots sh ON sh.id = r.shot_id "
            "JOIN variants v ON v.id = r.variant_id JOIN assets a ON a.id = v.asset_id WHERE sh.project_id = ? "
            "ORDER BY r.shot_id, r.position", (project_id,))
        for sh in shot_rows:
            sh["refs"] = [r for r in refs if r["shot_id"] == sh["id"]]
            sh["template_explicit"] = bool(sh["template_explicit"])
            sh["sound"] = json.loads(sh["sound"]) if sh.get("sound") else None
        project["cast"] = await s.library.cast(project_id)
        project |= await storyboard_api.project_extras(s, project_id)
        project |= await autonomy_api.project_extras(s, project_id)
        for sh in shot_rows:
            sh["locked"] = bool(sh["locked"])
        return project | {"shots": shot_rows, "jobs": jobs, "takes": takes, "approvals": approvals,
                          "director_busy": s.chat.busy(project_id), "last_event_id": await s.bus.last_event_id()}

    # shots -----------------------------------------------------------------

    def shot_error(exc):
        invalid_input = isinstance(exc, (shots.ShotError, UnknownTemplate))
        return HTTPException(422 if invalid_input else 409, str(exc))

    @app.get("/api/templates")
    async def templates(request: Request, _=owner):
        s = st(request)
        require_wangp(s)
        return await s.renders.video_templates()

    @app.get("/api/queue")
    async def queue(request: Request, _=owner):
        """WanGP's shared queue as FilmLab sees it: what Approve & render would wait behind."""
        s = st(request)
        require_wangp(s)
        q = await s.wangp.session("list_queue")
        return summarize_queue(q, s.wangp.agent_context)

    def require_wangp(s: Studio):
        if not s.wangp_ready.is_set():
            raise HTTPException(503, "WanGP is not connected; start WanGP with MCP enabled")

    @app.post("/api/projects/{project_id}/shots", status_code=201)
    async def create_shot(project_id: str, body: ShotIn, request: Request, _=owner):
        s = st(request)
        await project_or_404(s, project_id)
        require_wangp(s)
        try:
            tpl, explicit = await s.new_shot_template(project_id, body.template)
            if body.scene_id:
                await s.storyboard.scene(body.scene_id, project_id)
            shot = await shots.create_shot(s.db, s.bus, project_id, body.model_dump(exclude_none=True,
                                                                                     exclude={"scene_id"}),
                                           tpl, "owner", explicit, scene_id=body.scene_id)
            await s.storyboard.renumber(project_id)
            return await shots.get_shot(s.db, shot["id"])
        except (shots.ShotError, RenderError, StoryboardError) as exc:
            raise shot_error(exc) from exc

    @app.patch("/api/shots/{shot_id}")
    async def update_shot(shot_id: str, body: ShotIn, request: Request, _=owner):
        s = st(request)
        shot = await shot_or_404(s, shot_id)
        if body.template is not None:
            require_wangp(s)
        try:
            follow = body.template == ""
            tpl = (await s.new_shot_template(shot["project_id"], None))[0] if follow else (
                await s.renders.resolve_template(body.template) if body.template else None)
            return await shots.update_shot(s.db, s.bus, shot, body.model_dump(exclude_none=True,
                                                                               exclude={"template", "scene_id"}),
                                           "owner", tpl, follow_defaults=follow)
        except (shots.ShotError, RenderError) as exc:
            raise shot_error(exc) from exc

    @app.post("/api/shots/{shot_id}/validate")
    async def validate_shot(shot_id: str, request: Request, _=owner):
        s = st(request)
        try:
            return await s.renders.validate(await shot_or_404(s, shot_id))
        except Exception as exc:
            raise HTTPException(502, f"WanGP validation failed: {exc}") from exc

    @app.post("/api/shots/{shot_id}/approve-and-render")
    async def approve_and_render(shot_id: str, request: Request, body: RenderIn | None = None, _=owner):
        """The only path to a render: an authenticated owner action."""
        s = st(request)
        require_wangp(s)
        before = await shot_or_404(s, shot_id)
        try:
            shot = await shots.approve(s.db, s.bus, before, "owner")
            return await s.renders.submit(shot, "owner", preview=bool(body and body.preview))
        except (shots.ShotError, RenderError) as exc:
            # Refused before a job existed (blocked model, active render, …): the shot goes back to how it was.
            await shots.undo_approve(s.db, s.bus, before, "owner")
            raise shot_error(exc) from exc

    @app.post("/api/shots/{shot_id}/reject")
    async def reject_shot(shot_id: str, request: Request, _=owner):
        s = st(request)
        try:
            return await shots.reject(s.db, s.bus, await shot_or_404(s, shot_id), "owner")
        except shots.ShotError as exc:
            raise shot_error(exc) from exc

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, request: Request, _=owner):
        try:
            return await st(request).renders.cancel(job_id, "owner")
        except RenderError as exc:
            raise shot_error(exc) from exc

    # chat ------------------------------------------------------------------

    @app.get("/api/projects/{project_id}/chat")
    async def chat_history(project_id: str, request: Request, _=owner):
        s = st(request)
        await project_or_404(s, project_id)
        rows = await s.db.fetchall(
            "SELECT id, role, content, tool_calls, tool_call_id, created_at FROM chat_messages WHERE project_id = ? "
            "ORDER BY id", (project_id,))
        # The director's steps for the cockpit: each tool call's name, arguments and whether it worked.
        ok = {}
        for r in rows:
            if r["role"] == "tool" and r["tool_call_id"]:
                try:
                    ok[r["tool_call_id"]] = not failed(json.loads(r["content"] or "{}"))
                except ValueError:
                    ok[r["tool_call_id"]] = False
        out = []
        for r in rows:
            if r["role"] not in ("user", "assistant", "error", "note"):
                continue
            calls = json.loads(r.pop("tool_calls")) if r["tool_calls"] else []
            r.pop("tool_call_id")
            r["steps"] = [{"name": c["function"]["name"], "arguments": c["function"]["arguments"][:4000],
                           "ok": ok.get(c["id"])} for c in calls]
            out.append(r)
        return out

    @app.post("/api/projects/{project_id}/chat", status_code=202)
    async def chat_send(project_id: str, body: ChatIn, request: Request, _=owner):
        s = st(request)
        await project_or_404(s, project_id)
        try:
            return {"turn_id": await s.chat.send(project_id, body.text)}
        except ChatBusy as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/shots/{shot_id}/enhance", status_code=202)
    async def shot_enhance(shot_id: str, request: Request, _=owner):
        """Ask the director to pick references (if none) and rewrite the prompt around <Picture N>."""
        s = st(request)
        try:
            shot = await shots.get_shot(s.db, shot_id)
        except shots.ShotError as exc:
            raise HTTPException(404, str(exc)) from exc
        if shot["locked"]:
            raise HTTPException(409, "the shot is locked; unlock it to let the director change it")
        if not can_enhance(shot):
            raise HTTPException(409, f"shot is {shot['status']}; it can't be enhanced while it is approved or rendering")
        if not shot["idea"]:  # first Enhance of a director-drafted shot: its current prompt is the starting idea
            await s.db.execute("UPDATE shots SET idea = ? WHERE id = ?", (shot["prompt"], shot_id))
            shot = shot | {"idea": shot["prompt"]}
        scene = shot["scene_id"] and await s.db.fetchone("SELECT * FROM scenes WHERE id = ?", (shot["scene_id"],))
        project = await s.db.fetchone("SELECT brief, logline, style FROM projects WHERE id = ?", (shot["project_id"],))
        brief = enhance_brief(shot, await s.library.shot_refs(shot_id), await s.library.cast(shot["project_id"]),
                              dict(scene) if scene else None, (project and (project["brief"] or project["logline"])) or "",
                              (project and project["style"]) or "")
        try:
            turn = await s.chat.send(shot["project_id"], brief, expect=("update_shot", "the new prompt"))
        except ChatBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        await s.db.audit("owner", "shot.enhance", shot_id, {"turn_id": turn})
        return {"turn_id": turn}

    # media -----------------------------------------------------------------

    @app.get("/api/media/{media_id}")
    async def media(media_id: str, request: Request, _=owner):
        s = st(request)
        row = await s.db.fetchone("SELECT kind, rel_path FROM media WHERE id = ?", (media_id,))
        if not row:
            raise HTTPException(404, "no such media")
        root = settings.media_dir.resolve()
        path = (root / row["rel_path"]).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "media file missing")
        return FileResponse(path, media_type="video/mp4" if row["kind"] == "video" else "image/jpeg")

    # events ----------------------------------------------------------------

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        s: Studio = websocket.app.state.studio
        origin = websocket.headers.get("origin")
        sess = await auth.session_for(s.db, websocket.cookies.get(auth.COOKIE))
        # Browsers always send Origin on WebSocket upgrades; once reachable from the LAN, require it.
        origin_ok = origin in settings.allowed_origins if origin or not settings.loopback_only else True
        if not sess or not origin_ok:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        q = s.bus.subscribe()  # subscribe before replay so nothing falls in the gap
        try:
            after = int(websocket.query_params.get("after_event_id") or 0)
            replayed = await s.bus.replay(after) if after else []
            for ev in replayed:
                await websocket.send_json(ev)
            await websocket.send_json({"schema": "filmlab.event.v1", "event_id": None, "type": "stream.ready",
                                       "payload": {"last_event_id": await s.bus.last_event_id(),
                                                   "replayed": len(replayed)}})
            if s.gpu.latest:  # the stats bars fill at once instead of after the next sample
                await websocket.send_json({"schema": "filmlab.event.v1", "event_id": None, "type": "system.gpu",
                                           "payload": s.gpu.latest})
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    ev = {"schema": "filmlab.event.v1", "event_id": None, "type": "heartbeat", "payload": {}}
                await websocket.send_json(ev)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            s.bus.unsubscribe(q)

    # cockpit ---------------------------------------------------------------

    if settings.static_dir and settings.static_dir.is_dir():
        index = settings.static_dir / "index.html"
        app.mount("/assets", StaticFiles(directory=settings.static_dir / "assets"), name="assets")
        worker = settings.static_dir / "sw.js"

        @app.get("/sw.js", include_in_schema=False)
        async def service_worker():
            """Only shows "render finished" notifications (phones need a worker for them); it caches nothing."""
            if not worker.is_file():
                raise HTTPException(404)
            return FileResponse(worker, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self' ws: wss:; "
            "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        return response

    app.add_middleware(ClientNetworks, nets=settings.allowed_nets)
    return app


def run():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    if not settings.loopback_only and not (settings.ssl_certfile and settings.ssl_keyfile):
        raise SystemExit(f"refusing to listen on {settings.host} without TLS (set FILMLAB_SSL_CERTFILE/KEYFILE)")
    if not settings.loopback_only and not settings.secure_cookies:
        raise SystemExit("refusing to listen beyond loopback without FILMLAB_SECURE_COOKIES=1")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, proxy_headers=False,
                log_level="info", ws_ping_interval=30,
                # Open cockpit streams would otherwise hold a stop for as long as a browser stays connected.
                timeout_graceful_shutdown=5,
                ssl_certfile=settings.ssl_certfile, ssl_keyfile=settings.ssl_keyfile)


if __name__ == "__main__":
    run()
