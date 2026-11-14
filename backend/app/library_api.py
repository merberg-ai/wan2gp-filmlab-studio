"""Cockpit API for F3: reference library, image generation, model settings and shot references."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import auth, shots, sound
from .capabilities import CapabilityError
from .director.chat import ChatBusy
from .db import now
from .library import MAX_UPLOAD_BYTES, LibraryError
from .renders import RenderError, UnknownTemplate
from .tuning import TuningError
from .wangp.client import WanGPError
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


class SwapLookIn(BaseModel):
    to_variant_id: str
    from_variant_id: str | None = None  # only the shots using this look (default: every look of the asset)


@router.post("/api/library/assets/{asset_id}/swap-look")
async def swap_look(asset_id: str, body: SwapLookIn, request: Request):
    """Point every shot that uses the asset at another of its looks, keeping each reference's place (and the image
    at the same position in the new look, else its first). Locked shots and shots that are rendering are skipped."""
    s = st(request)
    try:
        asset = await s.library.asset(asset_id)
    except LibraryError as exc:
        raise HTTPException(404, str(exc)) from exc
    variants = {v["id"]: v for v in asset["variants"]}
    target = variants.get(body.to_variant_id)
    if not target:
        raise HTTPException(422, "that look isn't part of this asset")
    images = {vid: [f["file_id"] for f in v["files"] if f["kind"] == "image"] for vid, v in variants.items()}
    if not images[target["id"]]:
        raise HTTPException(422, f"the look “{target['name']}” v{target['version']} has no images")
    changed, skipped = [], []
    for u in await s.trash.usage(asset_id=asset_id):
        if body.from_variant_id and u["variant_id"] != body.from_variant_id:
            continue
        shot = await shots.get_shot(s.db, u["shot_id"])
        where = {"shot_id": shot["id"], "project_title": u["project_title"], "position": shot["position"],
                 "title": shot["title"]}
        if shot["locked"] or shot["status"] not in shots.REFS_EDITABLE:
            skipped.append(where | {"reason": "it's locked" if shot["locked"] else f"it's {shot['status']}"})
            continue
        refs, moved = [], False
        for r in await s.library.shot_refs(shot["id"]):
            if r["asset_id"] == asset_id and r["variant_id"] != target["id"] and \
                    (not body.from_variant_id or r["variant_id"] == body.from_variant_id):
                old = images.get(r["variant_id"], [])
                idx = old.index(r["file_id"]) if r["file_id"] in old else 0
                new = images[target["id"]]
                refs.append({"variant_id": target["id"], "file_id": new[min(idx, len(new) - 1)], "role": r["role"]})
                moved = True
            else:
                refs.append({"variant_id": r["variant_id"], "file_id": r["file_id"], "role": r["role"]})
        if not moved:
            continue
        try:
            await s.library.set_shot_refs(shot, refs, OWNER,
                                          rationale=f"{asset['name']}: look “{target['name']}” v{target['version']}")
            await shots.refs_changed(s.db, s.bus, shot, OWNER)
            changed.append(where)
        except (LibraryError, shots.ShotError) as exc:
            skipped.append(where | {"reason": str(exc)})
    await s.db.audit(OWNER, "library.swap_look", asset_id, {"to": target["id"], "shots": [c["shot_id"] for c in changed]})
    return {"changed": changed, "skipped": skipped}


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


class CastIn(BaseModel):
    asset_ids: list[str] = Field(default_factory=list, max_length=200)


@router.get("/api/projects/{project_id}/cast")
async def get_cast(project_id: str, request: Request):
    s = st(request)
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")
    return {"cast": await s.library.cast(project_id)}


@router.put("/api/projects/{project_id}/cast")
async def put_cast(project_id: str, body: CastIn, request: Request):
    """The Cast panel: exactly these assets are the film's cast (in "cast" mode, the only ones the director uses)."""
    s = st(request)
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")
    try:
        return {"cast": await s.library.set_cast(project_id, body.asset_ids, OWNER)}
    except LibraryError as exc:
        raise HTTPException(422, str(exc)) from exc


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


class CropIn(BaseModel):
    x: float
    y: float
    w: float
    h: float


class GrabIn(BaseModel):
    media_id: str | None = None  # a take's video
    file_id: str | None = None   # or a library picture
    t: float = Field(default=0.0, ge=0, le=36000)
    crop: CropIn | None = None


class ExtractEditIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    template: str | None = None
    count: int | None = Field(default=None, ge=1, le=4)
    resolution: str | None = None


class ExtractIn(BaseModel):
    file_id: str
    kind: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    tags: str = Field(default="", max_length=500)
    source_note: str = Field(default="", max_length=500)
    edit: ExtractEditIn | None = None


@router.post("/api/library/frames", status_code=201)
async def grab_frame(body: GrabIn, request: Request):
    """A frame of a take (or a crop of a picture), stored as a library image for Extract."""
    from .extract import ExtractError
    try:
        return await st(request).extractor.grab(body.media_id, body.file_id, body.t,
                                                body.crop.model_dump() if body.crop else None)
    except (ExtractError, LibraryError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/library/frames/upload", status_code=201)
async def upload_frame(request: Request):
    """A picture from the owner's computer for Extract (raw body), not yet in any asset."""
    s = st(request)
    data = await _body(request)
    try:
        f = await s.library.store_bytes(data, "upload")
    except LibraryError as exc:
        raise bad(exc) from exc
    if f["kind"] != "image":
        raise HTTPException(422, "Extract takes a picture (PNG, JPEG or WebP)")
    return {"id": f["id"], "file_id": f["id"], "width": f["width"], "height": f["height"], "source_note": "Uploaded picture"}


@router.post("/api/library/extract", status_code=201)
async def extract(body: ExtractIn, request: Request):
    """A new asset from an extracted picture: as its look, or reworked by a WanGP image model first (uses the GPU)."""
    from .extract import ExtractError
    s = st(request)
    if body.edit:
        wangp_ready(s)
    fields = {"kind": body.kind, "name": body.name, "description": body.description, "tags": body.tags}
    try:
        return await s.extractor.create(s, fields, body.file_id, body.source_note,
                                        body.edit.model_dump() if body.edit else None)
    except (ExtractError, LibraryError) as exc:
        raise HTTPException(422, str(exc)) from exc


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


class TuningIn(BaseModel):
    template: str = Field(min_length=1, max_length=200)
    values: dict = Field(default_factory=dict)


@router.get("/api/projects/{project_id}/tuning")
async def get_tuning(project_id: str, template: str, request: Request):
    """The generation settings one video template's model offers (WanGP's Advanced mode), with the project's own
    values. Asked of WanGP, so it needs WanGP running."""
    s = st(request)
    wangp_ready(s)
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")
    try:
        return await s.tuning.view(project_id, template)
    except (UnknownTemplate, WanGPError) as exc:
        raise HTTPException(422, str(exc)[:300]) from exc


@router.put("/api/projects/{project_id}/tuning")
async def put_tuning(project_id: str, body: TuningIn, request: Request):
    """Save the project's values for one template (empty: back to the template's own). Applies to renders started
    from now on; takes already made keep theirs."""
    s = st(request)
    wangp_ready(s)
    if not await s.db.fetchone("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)):
        raise HTTPException(404, "no such project")
    try:
        out = await s.tuning.save(project_id, body.template, body.values, OWNER)
    except (TuningError, UnknownTemplate, WanGPError) as exc:
        raise HTTPException(422, str(exc)[:600]) from exc
    await s.bus.publish("project.updated", {"project_id": project_id}, project_id)
    return out


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


@router.get("/api/projects/{project_id}/checks")
async def project_checks(project_id: str, request: Request):
    """Every shot's pre-render problems (the same checks as a render), for the warnings on the shot cards. Only
    from what FilmLab already knows (the saved capability matrix): it never asks WanGP, and a check that can't run
    just leaves that shot out."""
    s = st(request)
    project = await s.db.fetchone("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if not project:
        raise HTTPException(404, "no such project")
    return {"shots": await _project_issues(s, project)}


class FixPromptsIn(BaseModel):
    shot_ids: list[str] | None = None  # default: every shot with a warning the prompt can fix


@router.post("/api/projects/{project_id}/fix-prompts", status_code=202)
async def fix_prompts(project_id: str, body: FixPromptsIn, request: Request):
    """Ask the director to fix the flagged shots' prompts (a missing place or time of day, a picture the prompt names
    that the shot doesn't have, …), one update_shot each, changing only what's listed. Locked shots are left out."""
    s = st(request)
    project = await s.db.fetchone("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if not project:
        raise HTTPException(404, "no such project")
    issues = {sid: [i for i in found if i.get("kind") != "speaker"]  # who speaks is the shot's Sound and references
              for sid, found in (await _project_issues(s, project)).items()}
    rows = await s.db.fetchall("SELECT * FROM shots WHERE project_id = ? AND deleted_at IS NULL ORDER BY position",
                               (project_id,))
    todo = [r for r in rows if r["id"] in issues and not r["locked"] and r["status"] in shots.EDITABLE
            and (body.shot_ids is None or r["id"] in body.shot_ids)
            and any(i["level"] == "warn" for i in issues[r["id"]])]
    if not todo:
        raise HTTPException(409, "no unlocked shot has a warning its prompt can fix")
    lines = []
    for r in todo:
        found = "; ".join(i["message"].replace(" ✨ Enhance can rewrite it", "").rstrip(". ") for i in issues[r["id"]]
                          if i["level"] == "warn")
        lines.append(f"- shot {r['position']} “{r['title']}” (shot_id {r['id']}): {found}.")
    text = ("Fix these shots' prompts. For each one, call update_shot with the fixed prompt and a short rationale. Change "
            "only what's listed: keep the characters, the action, the camera, the <Picture N> names, the sound and the "
            "length as they are. Every shot is generated on its own, so its prompt must state its place and time of "
            "day.\n" + "\n".join(lines))
    try:
        turn = await s.chat.send(project_id, text, max_rounds=2 * len(todo) + 3, expect=("update_shot", "the prompt fixes"))
    except ChatBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    await s.db.audit(OWNER, "director.fix_prompts", project_id, {"shots": [r["id"] for r in todo]})
    return {"turn_id": turn, "shots": [r["id"] for r in todo]}


async def _project_issues(s, project: dict) -> dict[str, list[dict]]:
    """Every shot's warnings and blocks, from the saved capability matrix (never asking WanGP)."""
    project_id = project["id"]
    if not await s.caps.built_at():
        return {}
    out = {}
    for shot in await s.db.fetchall("SELECT * FROM shots WHERE project_id = ? AND deleted_at IS NULL", (project_id,)):
        try:
            refs = await s.library.shot_refs(shot["id"])
            template = await s.caps.effective_template(shot, project, len(refs))
            snd = sound.load(shot["sound"])
            issues = await s.caps.checks(template, len(refs), shot["prompt"], await s.caps.setting_for(shot),
                                         sound_fields=snd, refs=refs, voiced=await s.refs.voiced(snd, refs))
        except Exception:  # noqa: BLE001 - one shot's check never hides the others'
            continue
        shown = [i for i in issues if i["level"] in ("block", "warn") and "downloaded" not in i["message"]]
        if shown:
            out[shot["id"]] = shown
    return out


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
