"""Cockpit API for F3: reference library, image generation, model settings and shot references."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import auth, shots, sound
from .capabilities import CapabilityError
from .db import now
from .library import MAX_UPLOAD_BYTES, LibraryError
from .renders import RenderError
from .trash import TrashError

router = APIRouter(dependencies=[Depends(auth.require_owner)])
OWNER = "owner"


def st(request: Request):
    return request.app.state.studio


def wangp_ready(s):
    if not s.wangp_ready.is_set():
        raise HTTPException(503, "WanGP is not connected; start WanGP with MCP enabled")


def bad(exc: Exception) -> HTTPException:
    return HTTPException(422 if isinstance(exc, (LibraryError, CapabilityError, shots.ShotError)) else 409, str(exc))


class AssetIn(BaseModel):
    kind: str | None = None
    name: str | None = None
    description: str | None = None
    notes: str | None = None
    tags: str | None = None


class VariantIn(BaseModel):
    name: str | None = None
    description: str | None = None


class LockIn(BaseModel):
    locked: bool


class FileMeta(BaseModel):
    caption: str | None = None
    source_note: str | None = None
    license_note: str | None = None


class AttachIn(FileMeta):
    file_id: str


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    template: str | None = None
    input_file_ids: list[str] = Field(default_factory=list, max_length=8)
    count: int | None = Field(default=None, ge=1, le=4)
    resolution: str | None = None
    seed: int | None = None


class RefIn(BaseModel):
    variant_id: str
    file_id: str | None = None
    role: str = ""


class RefsIn(BaseModel):
    refs: list[RefIn] = Field(max_length=8)


class ProjectModelsIn(BaseModel):
    video_refs_template: str | None = None
    video_plain_template: str | None = None


# --- library ---------------------------------------------------------------------

@router.get("/api/library")
async def search(request: Request, q: str = "", kind: str | None = None):
    return await st(request).library.search(q, kind)


@router.post("/api/library/assets", status_code=201)
async def create_asset(body: AssetIn, request: Request):
    try:
        return await st(request).library.create_asset(body.model_dump(exclude_none=True), OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


async def asset_view(s, asset_id: str) -> dict:
    asset = await s.library.asset(asset_id)
    asset["images"] = await s.images.for_asset(asset_id)
    asset["image_requests"] = await s.db.fetchall(
        "SELECT r.* FROM image_requests r JOIN variants v ON v.id = r.variant_id WHERE v.asset_id = ? "
        "AND r.status = 'pending' ORDER BY r.created_at", (asset_id,))
    for r in asset["image_requests"]:
        r["input_file_ids"] = json.loads(r["input_file_ids"])
    return asset


@router.get("/api/library/assets/{asset_id}")
async def get_asset(asset_id: str, request: Request):
    try:
        return await asset_view(st(request), asset_id)
    except LibraryError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/api/library/assets/{asset_id}")
async def update_asset(asset_id: str, body: AssetIn, request: Request):
    try:
        return await st(request).library.update_asset(asset_id, body.model_dump(exclude_none=True, exclude={"kind"}), OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


@router.post("/api/library/assets/{asset_id}/variants", status_code=201)
async def create_variant(asset_id: str, body: VariantIn, request: Request):
    try:
        return await st(request).library.create_variant(asset_id, body.name or "", body.description or "", OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


@router.patch("/api/library/variants/{variant_id}")
async def update_variant(variant_id: str, body: VariantIn, request: Request):
    try:
        return await st(request).library.update_variant(variant_id, body.model_dump(exclude_none=True), OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


@router.get("/api/library/assets/{asset_id}/usage")
async def asset_usage(asset_id: str, request: Request):
    """Live shots that use the asset: what deleting it would take the reference off."""
    return await st(request).trash.usage(asset_id=asset_id)


@router.get("/api/library/variants/{variant_id}/usage")
async def variant_usage(variant_id: str, request: Request):
    return await st(request).trash.usage(variant_id=variant_id)


@router.delete("/api/library/assets/{asset_id}")
async def delete_asset(asset_id: str, request: Request):
    try:
        return await st(request).trash.delete_asset(asset_id, OWNER)
    except (TrashError, LibraryError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/api/library/variants/{variant_id}")
async def delete_variant(variant_id: str, request: Request):
    try:
        return await st(request).trash.delete_variant(variant_id, OWNER)
    except (TrashError, LibraryError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/library/variants/{variant_id}/lock")
async def lock_variant(variant_id: str, body: LockIn, request: Request):
    try:
        return await st(request).library.set_locked(variant_id, body.locked, OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


@router.post("/api/library/variants/{variant_id}/new-version", status_code=201)
async def new_version(variant_id: str, request: Request):
    try:
        return await st(request).library.new_version(variant_id, OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


async def _body(request: Request) -> bytes:
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    return bytes(data)


@router.put("/api/library/assets/{asset_id}/voice")
async def set_voice(asset_id: str, request: Request):
    """Raw-body upload of a character's voice sample."""
    s = st(request)
    try:
        return await s.library.set_voice(asset_id, await _body(request), OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


@router.delete("/api/library/assets/{asset_id}/voice")
async def clear_voice(asset_id: str, request: Request):
    try:
        return await st(request).library.clear_voice(asset_id, OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


@router.post("/api/library/variants/{variant_id}/files", status_code=201)
async def upload_file(variant_id: str, request: Request, caption: str = "", source_note: str = "", license_note: str = ""):
    """Raw-body upload (one file per request; the cockpit sends several in a row)."""
    s = st(request)
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        await s.library.mutable_variant(variant_id)  # refuse before storing anything
        f = await s.library.store_bytes(bytes(data), "upload")
        entry = await s.library.add_file(variant_id, f["id"], {"caption": caption, "source_note": source_note,
                                                               "license_note": license_note}, OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc
    await s.db.audit(OWNER, "library.upload", f["id"], {"variant_id": variant_id, "dedup": f["deduplicated"]})
    return {"file_id": f["id"], "deduplicated": f["deduplicated"], **entry}


@router.post("/api/library/variants/{variant_id}/attach", status_code=201)
async def attach_file(variant_id: str, body: AttachIn, request: Request):
    try:
        return await st(request).library.add_file(variant_id, body.file_id, body.model_dump(exclude={"file_id"}), OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


@router.patch("/api/library/entries/{entry_id}")
async def update_entry(entry_id: str, body: FileMeta, request: Request):
    try:
        return await st(request).library.update_file_entry(entry_id, body.model_dump(exclude_none=True), OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


@router.delete("/api/library/entries/{entry_id}")
async def remove_entry(entry_id: str, request: Request):
    try:
        await st(request).library.remove_file_entry(entry_id, OWNER)
        return {"ok": True}
    except LibraryError as exc:
        raise bad(exc) from exc


@router.get("/api/library/files/{file_id}")
async def get_file(file_id: str, request: Request, thumb: bool = False):
    lib = st(request).library
    try:
        row = await lib.file(file_id)
        path = lib.path(row, thumb=thumb and bool(row["thumb_rel_path"]))
    except LibraryError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, "file missing on disk")
    mime = "image/jpeg" if thumb and row["thumb_rel_path"] else row["mime"]
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


# --- image generation ---------------------------------------------------------------

@router.post("/api/library/variants/{variant_id}/generate", status_code=201)
async def generate(variant_id: str, body: GenerateIn, request: Request):
    """Owner-only: starts a WanGP image job (uses the GPU)."""
    s = st(request)
    wangp_ready(s)
    try:
        return await s.images.submit(variant_id, body.prompt, OWNER, body.template, body.input_file_ids, body.count,
                                     body.resolution, body.seed)
    except (LibraryError, RenderError) as exc:
        raise bad(exc) from exc


@router.post("/api/library/candidates/{candidate_id}/keep")
async def keep(candidate_id: str, body: FileMeta, request: Request):
    try:
        return await st(request).images.keep(candidate_id, body.model_dump(exclude_none=True), OWNER)
    except LibraryError as exc:
        raise bad(exc) from exc


@router.post("/api/library/candidates/{candidate_id}/discard")
async def discard(candidate_id: str, request: Request):
    try:
        await st(request).images.discard(candidate_id, OWNER)
        return {"ok": True}
    except LibraryError as exc:
        raise bad(exc) from exc


@router.post("/api/image-jobs/{job_id}/cancel")
async def cancel_image(job_id: str, request: Request):
    try:
        return await st(request).images.cancel(job_id, OWNER)
    except RenderError as exc:
        raise bad(exc) from exc


@router.post("/api/library/image-requests/{request_id}/approve", status_code=201)
async def approve_image_request(request_id: str, request: Request):
    s = st(request)
    wangp_ready(s)
    r = await s.db.fetchone("SELECT * FROM image_requests WHERE id = ? AND status = 'pending'", (request_id,))
    if not r:
        raise HTTPException(404, "no such pending request")
    try:
        job = await s.images.submit(r["variant_id"], r["prompt"], OWNER, r["template"], json.loads(r["input_file_ids"]),
                                    r["count"])
    except (LibraryError, RenderError) as exc:
        raise bad(exc) from exc
    await s.db.execute("UPDATE image_requests SET status = 'approved', decided_at = ? WHERE id = ?", (now(), request_id))
    await s.db.audit(OWNER, "image_request.approve", request_id, {"job_id": job["id"]})
    return job


@router.post("/api/library/image-requests/{request_id}/reject")
async def reject_image_request(request_id: str, request: Request):
    s = st(request)
    await s.db.execute("UPDATE image_requests SET status = 'rejected', decided_at = ? WHERE id = ? AND status = 'pending'",
                       (now(), request_id))
    await s.db.audit(OWNER, "image_request.reject", request_id)
    return {"ok": True}


# --- settings and capabilities -----------------------------------------------------------

@router.get("/api/capabilities")
async def capabilities(request: Request, refresh: bool = False):
    s = st(request)
    wangp_ready(s)
    matrix = await s.caps.matrix(refresh=refresh)
    return {"built_at": await s.caps.built_at(), "templates": matrix, "settings": await s.caps.settings()}


@router.put("/api/settings/templates")
async def update_settings(body: dict, request: Request):
    s = st(request)
    wangp_ready(s)
    try:
        return await s.caps.update_settings(body, OWNER)
    except CapabilityError as exc:
        raise bad(exc) from exc


@router.patch("/api/projects/{project_id}/models")
async def project_models(project_id: str, body: ProjectModelsIn, request: Request):
    """Per-project override of the default video templates; an empty string clears it."""
    s = st(request)
    wangp_ready(s)
    matrix = await s.caps.matrix()
    changes = body.model_dump(exclude_none=True)
    try:
        for key, value in changes.items():
            if value:
                tasks = ("gen_video", "gen_video_with_refs") if key == "video_refs_template" else ("gen_video",)
                s.caps.ensure_usable(matrix, value, tasks)
    except CapabilityError as exc:
        raise bad(exc) from exc
    for key, value in changes.items():
        await s.db.execute(f"UPDATE projects SET {key} = ? WHERE id = ?", (value or None, project_id))
    await s.db.audit(OWNER, "project.models", project_id, changes)
    await s.bus.publish("project.updated", {"project_id": project_id}, project_id)
    return await s.db.fetchone("SELECT id, video_refs_template, video_plain_template FROM projects WHERE id = ?",
                               (project_id,))


# --- shot references --------------------------------------------------------------------------

@router.get("/api/shots/{shot_id}/refs")
async def get_refs(shot_id: str, request: Request):
    return await st(request).library.shot_refs(shot_id)


@router.put("/api/shots/{shot_id}/refs")
async def set_refs(shot_id: str, body: RefsIn, request: Request):
    s = st(request)
    try:
        shot = await shots.get_shot(s.db, shot_id)
        if shot["status"] not in shots.REFS_EDITABLE:
            raise shots.ShotError(f"shot is {shot['status']}; references can't change while it renders")
        refs = await s.library.set_shot_refs(shot, [r.model_dump() for r in body.refs], OWNER)
        await s.refs.sync_template_soon(shot, len(refs))
        await shots.refs_changed(s.db, s.bus, shot, OWNER)
    except (LibraryError, shots.ShotError) as exc:
        raise bad(exc) from exc
    return refs


@router.get("/api/shots/{shot_id}/checks")
async def shot_checks(shot_id: str, request: Request):
    s = st(request)
    wangp_ready(s)
    shot = await shots.get_shot(s.db, shot_id)
    refs = await s.library.shot_refs(shot_id)
    project = await s.db.fetchone("SELECT * FROM projects WHERE id = ?", (shot["project_id"],))
    template = await s.caps.effective_template(shot, project, len(refs))
    return {"template": template, "follows_defaults": not shot["template_explicit"], "refs": len(refs),
            "issues": await s.caps.checks(template, len(refs), shot["prompt"], await s.caps.setting_for(shot),
                                          sound_fields=(snd := sound.load(shot["sound"])), refs=refs,
                                          voiced=await s.refs.voiced(snd, refs))
            + [{"level": "warn", "message": m} for m in (await s.health.gpu_share_note(),) if m]
            + [{"level": "block", "message": m} for m in (await s.gpu_held(),) if m]}
