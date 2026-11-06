"""Recoverable deletion ("Recently deleted") for projects, shots, library assets and looks.

Deleting sets deleted_at: the item disappears from every normal view and lookup, and can be restored.
Deleting a library asset or look also removes it from the shots that use it (they're listed first, so
the owner knows); takes already made keep their own record of what they used. "Delete forever" removes
the rows and the files on disk that nothing else uses. Nothing here touches WanGP: its gallery items
and outputs are WanGP's.
"""

import asyncio
import logging
import shutil
from pathlib import Path

from . import shots
from .db import Database, now
from .events import EventBus
from .renders import ACTIVE

log = logging.getLogger("filmlab.trash")
KINDS = ("project", "shot", "take", "asset", "variant")
_ACTIVE = "(" + ", ".join(f"'{s}'" for s in ACTIVE) + ")"


class TrashError(ValueError):
    pass


class Trash:
    def __init__(self, db: Database, bus: EventBus, library, refs, media_dir: Path):
        self.db, self.bus, self.library, self.refs = db, bus, library, refs
        self.media_root = media_dir

    # --- what uses a library item ------------------------------------------

    async def usage(self, asset_id: str | None = None, variant_id: str | None = None) -> list[dict]:
        """Live shots that reference the asset (or one look of it)."""
        where, arg = ("v.asset_id = ?", asset_id) if asset_id else ("r.variant_id = ?", variant_id)
        return await self.db.fetchall(
            "SELECT DISTINCT s.id AS shot_id, s.position, s.title, s.status, p.id AS project_id, p.title AS project_title "
            "FROM shot_refs r JOIN shots s ON s.id = r.shot_id JOIN projects p ON p.id = s.project_id "
            f"JOIN variants v ON v.id = r.variant_id WHERE {where} AND s.deleted_at IS NULL AND p.deleted_at IS NULL "
            "ORDER BY p.title, s.position", (arg,))

    # --- delete (recoverable) ---------------------------------------------

    async def delete_project(self, project_id: str, actor: str) -> dict:
        p = await self.db.fetchone("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
        if not p:
            raise TrashError("no such project")
        if await self.db.fetchone(f"SELECT 1 FROM render_jobs WHERE project_id = ? AND status IN {_ACTIVE}", (project_id,)):
            raise TrashError("a render in this project is still running; cancel it first")
        await self._mark("projects", project_id)
        await self.db.audit(actor, "project.delete", project_id)
        await self.bus.publish("project.updated", {"project_id": project_id, "deleted": True}, project_id)
        return {"deleted": "project", "id": project_id}

    async def delete_shot(self, shot_id: str, actor: str) -> dict:
        shot = await shots.get_shot(self.db, shot_id)
        if await self.db.fetchone(f"SELECT 1 FROM render_jobs WHERE shot_id = ? AND status IN {_ACTIVE}", (shot_id,)):
            raise TrashError("this shot is rendering; cancel the render first")
        await self.db.execute("UPDATE approvals SET status = 'withdrawn', decided_at = ? WHERE shot_id = ? "
                              "AND status = 'pending'", (now(), shot_id))
        await self._mark("shots", shot_id)
        await self.db.audit(actor, "shot.delete", shot_id)
        await self.bus.publish("shot.updated", {"shot_id": shot_id, "deleted": True}, shot["project_id"])
        return {"deleted": "shot", "id": shot_id}

    async def delete_take(self, take_id: str, actor: str) -> dict:
        t = await self.db.fetchone("SELECT t.*, s.project_id FROM takes t JOIN shots s ON s.id = t.shot_id "
                                   "WHERE t.id = ? AND t.deleted_at IS NULL AND s.deleted_at IS NULL", (take_id,))
        if not t:
            raise TrashError("no such take")

        def write(c):
            c.execute("UPDATE takes SET deleted_at = ? WHERE id = ?", (now(), take_id))
            c.execute("UPDATE shots SET selected_take_id = NULL WHERE id = ? AND selected_take_id = ?", (t["shot_id"], take_id))
        await self.db.transaction(write)
        await self.db.audit(actor, "take.delete", take_id, {"shot_id": t["shot_id"]})
        await self.bus.publish("shot.updated", {"shot_id": t["shot_id"]}, t["project_id"])
        return {"deleted": "take", "id": take_id}

    async def delete_asset(self, asset_id: str, actor: str) -> dict:
        a = await self.db.fetchone("SELECT * FROM assets WHERE id = ? AND deleted_at IS NULL", (asset_id,))
        if not a:
            raise TrashError("no such asset")
        await self._refuse_if_generating("asset_id", asset_id)
        removed = await self._drop_refs(actor, asset_id=asset_id)
        await self.db.execute("UPDATE image_requests SET status = 'rejected', decided_at = ? WHERE status = 'pending' AND variant_id IN "
                              "(SELECT id FROM variants WHERE asset_id = ?)", (now(), asset_id))
        await self._mark("assets", asset_id)
        await self.db.audit(actor, "asset.delete", asset_id, {"removed_from_shots": removed})
        await self.bus.publish("library.updated", {"asset_id": asset_id, "deleted": True})
        return {"deleted": "asset", "id": asset_id, "removed_from_shots": removed}

    async def delete_variant(self, variant_id: str, actor: str) -> dict:
        v = await self.library.variant(variant_id)
        if v["locked"]:
            raise TrashError(f"'{v['name']}' v{v['version']} is locked; unlock it before deleting it")
        live = await self.db.fetchone("SELECT COUNT(*) AS n FROM variants WHERE asset_id = ? AND deleted_at IS NULL",
                                      (v["asset_id"],))
        if live["n"] <= 1:
            raise TrashError("this is the asset's only look; delete the asset instead")
        await self._refuse_if_generating("variant_id", variant_id)
        removed = await self._drop_refs(actor, variant_id=variant_id)
        await self.db.execute("UPDATE image_requests SET status = 'rejected', decided_at = ? WHERE status = 'pending' AND variant_id = ?",
                              (now(), variant_id))
        await self._mark("variants", variant_id)
        await self.db.audit(actor, "variant.delete", variant_id, {"removed_from_shots": removed})
        await self.bus.publish("library.updated", {"asset_id": v["asset_id"], "deleted_variant": variant_id})
        return {"deleted": "variant", "id": variant_id, "removed_from_shots": removed}

    async def _mark(self, table: str, row_id: str):
        await self.db.execute(f"UPDATE {table} SET deleted_at = ?, updated_at = ? WHERE id = ?", (now(), now(), row_id))

    async def _refuse_if_generating(self, column: str, value: str):
        if await self.db.fetchone(f"SELECT 1 FROM image_jobs WHERE {column} = ? AND status IN {_ACTIVE}", (value,)):
            raise TrashError("an image is being generated for it; cancel that first")

    async def _drop_refs(self, actor: str, asset_id: str | None = None, variant_id: str | None = None) -> list[dict]:
        """Take the asset (or look) off every live shot that uses it; the remaining pictures are renumbered."""
        hit = (lambda r: r["asset_id"] == asset_id) if asset_id else (lambda r: r["variant_id"] == variant_id)
        removed = []
        for u in await self.usage(asset_id=asset_id, variant_id=variant_id):
            shot = await shots.get_shot(self.db, u["shot_id"])
            if shot["status"] not in shots.REFS_EDITABLE:
                raise TrashError(f"shot {shot['position']} “{shot['title']}” is rendering with it; wait or cancel first")
        for u in await self.usage(asset_id=asset_id, variant_id=variant_id):
            shot = await shots.get_shot(self.db, u["shot_id"])
            keep = [r for r in await self.library.shot_refs(shot["id"]) if not hit(r)]
            await self.library.set_shot_refs(shot, [{"variant_id": r["variant_id"], "file_id": r["file_id"],
                                                     "role": r["role"]} for r in keep], actor)
            await self.refs.sync_template_soon(shot, len(keep))
            await shots.refs_changed(self.db, self.bus, shot, actor)
            removed.append({"shot_id": shot["id"], "position": shot["position"], "title": shot["title"],
                            "project_title": u["project_title"]})
        return removed

    # --- recently deleted ------------------------------------------------

    async def listing(self) -> dict:
        takes = await self.db.fetchall(
            "SELECT t.id, t.verdict, t.deleted_at, t.thumb_media_id, s.id AS shot_id, s.position, s.title, "
            "p.id AS project_id, p.title AS project_title FROM takes t JOIN shots s ON s.id = t.shot_id "
            "JOIN projects p ON p.id = s.project_id WHERE t.deleted_at IS NOT NULL AND s.deleted_at IS NULL "
            "AND p.deleted_at IS NULL ORDER BY t.deleted_at DESC")
        projects, shot_rows, assets, variants = await self.db.fetchall_many(
            ("SELECT id, title, logline, deleted_at, (SELECT COUNT(*) FROM shots s WHERE s.project_id = p.id) AS shots "
             "FROM projects p WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC", ()),
            ("SELECT s.id, s.position, s.title, s.deleted_at, p.id AS project_id, p.title AS project_title, "
             "(SELECT COUNT(*) FROM takes t WHERE t.shot_id = s.id) AS takes FROM shots s JOIN projects p "
             "ON p.id = s.project_id WHERE s.deleted_at IS NOT NULL AND p.deleted_at IS NULL ORDER BY s.deleted_at DESC", ()),
            ("SELECT a.id, a.kind, a.name, a.deleted_at, (SELECT f.id FROM variant_files vf JOIN variants v "
             "ON v.id = vf.variant_id JOIN library_files f ON f.id = vf.file_id WHERE v.asset_id = a.id AND "
             "f.kind = 'image' LIMIT 1) AS cover_file_id FROM assets a WHERE a.deleted_at IS NOT NULL "
             "ORDER BY a.deleted_at DESC", ()),
            ("SELECT v.id, v.name, v.version, v.deleted_at, a.id AS asset_id, a.name AS asset_name, a.kind, "
             "(SELECT vf.file_id FROM variant_files vf JOIN library_files f ON f.id = vf.file_id WHERE "
             "vf.variant_id = v.id AND f.kind = 'image' LIMIT 1) AS cover_file_id FROM variants v JOIN assets a "
             "ON a.id = v.asset_id WHERE v.deleted_at IS NOT NULL AND a.deleted_at IS NULL ORDER BY v.deleted_at DESC", ()))
        return {"projects": projects, "shots": shot_rows, "takes": takes, "assets": assets, "variants": variants}

    async def restore(self, kind: str, row_id: str, actor: str) -> dict:
        table = self._table(kind)
        row = await self.db.fetchone(f"SELECT * FROM {table} WHERE id = ? AND deleted_at IS NOT NULL", (row_id,))
        if not row:
            raise TrashError(f"no deleted {kind} with that id")
        if kind == "shot" and await self._deleted("projects", row["project_id"]):
            raise TrashError("its project is deleted; restore the project first")
        if kind == "variant" and await self._deleted("assets", row["asset_id"]):
            raise TrashError("its asset is deleted; restore the asset first")
        if kind == "take":
            if await self._deleted("shots", row["shot_id"]):
                raise TrashError("its shot is deleted; restore the shot first")
            await self.db.execute("UPDATE takes SET deleted_at = NULL WHERE id = ?", (row_id,))
            await self.db.audit(actor, "take.restore", row_id)
            shot = await self.db.fetchone("SELECT project_id FROM shots WHERE id = ?", (row["shot_id"],))
            await self.bus.publish("shot.updated", {"shot_id": row["shot_id"]}, shot["project_id"])
            return {"restored": kind, "id": row_id}
        await self.db.execute(f"UPDATE {table} SET deleted_at = NULL, updated_at = ? WHERE id = ?", (now(), row_id))
        if kind == "shot":  # its scene may have gone meanwhile: it comes back in Unsorted
            await self.db.execute("UPDATE shots SET scene_id = NULL WHERE id = ? AND scene_id IN "
                                  "(SELECT id FROM scenes WHERE deleted_at IS NOT NULL)", (row_id,))
        await self.db.audit(actor, f"{kind}.restore", row_id)
        await self._announce(kind, row)
        return {"restored": kind, "id": row_id}

    async def _deleted(self, table: str, row_id: str) -> bool:
        return bool(await self.db.fetchone(f"SELECT 1 FROM {table} WHERE id = ? AND deleted_at IS NOT NULL", (row_id,)))

    async def _announce(self, kind: str, row: dict):
        if kind == "project":
            await self.bus.publish("project.updated", {"project_id": row["id"]}, row["id"])
        elif kind == "shot":
            await self.bus.publish("shot.updated", {"shot_id": row["id"]}, row["project_id"])
        else:
            await self.bus.publish("library.updated", {"asset_id": row.get("asset_id") or row["id"]})

    @staticmethod
    def _table(kind: str) -> str:
        if kind not in KINDS:
            raise TrashError(f"kind must be one of {', '.join(KINDS)}")
        return {"project": "projects", "shot": "shots", "take": "takes", "asset": "assets", "variant": "variants"}[kind]

    # --- delete forever ------------------------------------------------

    async def purge(self, kind: str, row_id: str, actor: str) -> dict:
        """Remove a deleted item's rows for good, and the files on disk nothing else uses."""
        table = self._table(kind)
        row = await self.db.fetchone(f"SELECT * FROM {table} WHERE id = ? AND deleted_at IS NOT NULL", (row_id,))
        if not row:
            raise TrashError("only items in Recently deleted can be deleted forever")
        if kind == "project":
            shot_ids = [r["id"] for r in await self.db.fetchall("SELECT id FROM shots WHERE project_id = ?", (row_id,))]
            media = await self._purge_shots(shot_ids)
            media += await self.db.fetchall("SELECT rel_path FROM media WHERE project_id = ?", (row_id,))

            def write(c):
                c.execute("DELETE FROM media WHERE project_id = ?", (row_id,))
                c.execute("DELETE FROM chat_messages WHERE project_id = ?", (row_id,))
                c.execute("DELETE FROM approvals WHERE project_id = ?", (row_id,))
                c.execute("DELETE FROM project_cast WHERE project_id = ?", (row_id,))
                c.execute("DELETE FROM batch_items WHERE batch_id IN (SELECT id FROM batches WHERE project_id = ?)", (row_id,))
                c.execute("DELETE FROM batches WHERE project_id = ?", (row_id,))
                c.execute("DELETE FROM batch_requests WHERE project_id = ?", (row_id,))
                c.execute("DELETE FROM redo_proposals WHERE project_id = ?", (row_id,))
                c.execute("DELETE FROM director_runs WHERE project_id = ?", (row_id,))
                c.execute("DELETE FROM scenes WHERE project_id = ?", (row_id,))
                for t in ("exports", "edit_suggestions", "edit_revisions", "edits"):
                    c.execute(f"DELETE FROM {t} WHERE project_id = ?", (row_id,))
                c.execute("DELETE FROM projects WHERE id = ?", (row_id,))
            await self.db.transaction(write)
            removed = await self._remove_media(media)
            await asyncio.to_thread(self._remove_dir, self.media_root / row_id)
        elif kind == "shot":
            removed = await self._remove_media(await self._purge_shots([row_id]))
        elif kind == "take":
            removed = await self._remove_media(await self._purge_take(row))
        else:
            variant_ids = ([r["id"] for r in await self.db.fetchall("SELECT id FROM variants WHERE asset_id = ?", (row_id,))]
                           if kind == "asset" else [row_id])
            await self._purge_variants(variant_ids, asset_id=row_id if kind == "asset" else None)
            removed = await self.collect_library_files()
        await self.db.audit(actor, f"{kind}.purge", row_id, {"files_removed": removed})
        return {"purged": kind, "id": row_id, "files_removed": removed}

    async def _purge_shots(self, shot_ids: list[str]) -> list[dict]:
        """Delete the shots' rows; return their media rows so the files can go after the commit."""
        if not shot_ids:
            return []
        marks = ", ".join("?" * len(shot_ids))
        media = await self.db.fetchall(
            f"SELECT m.id, m.rel_path FROM media m WHERE m.id IN (SELECT media_id FROM takes WHERE shot_id IN ({marks})) "
            f"OR m.id IN (SELECT thumb_media_id FROM takes WHERE shot_id IN ({marks}))", (*shot_ids, *shot_ids))
        media_ids = [m["id"] for m in media]

        def write(c):
            c.execute(f"UPDATE shots SET selected_take_id = NULL WHERE id IN ({marks})", shot_ids)
            c.execute(f"DELETE FROM take_inspections WHERE take_id IN (SELECT id FROM takes WHERE shot_id IN ({marks}))",
                      shot_ids)
            c.execute(f"DELETE FROM redo_proposals WHERE shot_id IN ({marks})", shot_ids)
            c.execute(f"DELETE FROM takes WHERE shot_id IN ({marks})", shot_ids)
            if media_ids:
                c.execute(f"DELETE FROM media WHERE id IN ({', '.join('?' * len(media_ids))})", media_ids)
            c.execute(f"DELETE FROM render_jobs WHERE shot_id IN ({marks})", shot_ids)
            c.execute(f"DELETE FROM approvals WHERE shot_id IN ({marks})", shot_ids)
            c.execute(f"DELETE FROM shot_refs WHERE shot_id IN ({marks})", shot_ids)
            c.execute(f"DELETE FROM shot_revisions WHERE shot_id IN ({marks})", shot_ids)
            c.execute(f"DELETE FROM batch_items WHERE shot_id IN ({marks})", shot_ids)
            c.execute(f"DELETE FROM shots WHERE id IN ({marks})", shot_ids)
        await self.db.transaction(write)
        return media

    async def _purge_take(self, take: dict) -> list[dict]:
        """A take's rows and media. Refused while a delivery was built from it: its verify needs the file."""
        used = await self.db.fetchone("SELECT number FROM exports WHERE deleted_at IS NULL AND kind = 'final' AND "
                                      "status = 'done' AND manifest LIKE ?", (f'%"{take["media_id"]}"%',))
        if used:
            raise TrashError(f"Delivery {used['number']} was made with this take; deleting it forever would stop that "
                             "delivery from verifying. Delete the delivery first, or keep the take here.")
        ids = [x for x in (take["media_id"], take["thumb_media_id"]) if x]
        media = await self.db.fetchall(f"SELECT id, rel_path FROM media WHERE id IN ({', '.join('?' * len(ids))})", ids)

        def write(c):
            c.execute("DELETE FROM take_inspections WHERE take_id = ?", (take["id"],))
            c.execute("UPDATE redo_proposals SET result_take_id = NULL WHERE result_take_id = ?", (take["id"],))
            c.execute("UPDATE redo_proposals SET take_id = NULL WHERE take_id = ?", (take["id"],))
            c.execute("DELETE FROM takes WHERE id = ?", (take["id"],))
            c.execute(f"DELETE FROM media WHERE id IN ({', '.join('?' * len(ids))})", ids)
        await self.db.transaction(write)
        return media

    async def _purge_variants(self, variant_ids: list[str], asset_id: str | None):
        marks = ", ".join("?" * len(variant_ids)) or "''"

        def write(c):
            jobs = f"SELECT id FROM image_jobs WHERE variant_id IN ({marks})"
            c.execute(f"DELETE FROM image_candidates WHERE job_id IN ({jobs})", variant_ids)
            c.execute(f"DELETE FROM image_jobs WHERE variant_id IN ({marks})", variant_ids)
            c.execute(f"DELETE FROM image_requests WHERE variant_id IN ({marks})", variant_ids)
            c.execute(f"DELETE FROM shot_refs WHERE variant_id IN ({marks})", variant_ids)
            c.execute(f"DELETE FROM variant_files WHERE variant_id IN ({marks})", variant_ids)
            c.execute(f"UPDATE variants SET parent_id = NULL WHERE parent_id IN ({marks})", variant_ids)
            c.execute(f"DELETE FROM variants WHERE id IN ({marks})", variant_ids)
            if asset_id:
                c.execute("UPDATE scenes SET location_asset_id = NULL WHERE location_asset_id = ?", (asset_id,))
                c.execute("DELETE FROM project_cast WHERE asset_id = ?", (asset_id,))
                c.execute("DELETE FROM library_search WHERE asset_id = ?", (asset_id,))
                c.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        await self.db.transaction(write)

    async def collect_library_files(self) -> int:
        """Remove library files no look, candidate or shot uses any more (rows, thumbnails, upload cache)."""
        orphans = await self.db.fetchall(
            "SELECT * FROM library_files f WHERE NOT EXISTS (SELECT 1 FROM variant_files WHERE file_id = f.id) "
            "AND NOT EXISTS (SELECT 1 FROM image_candidates WHERE file_id = f.id) "
            "AND NOT EXISTS (SELECT 1 FROM shot_refs WHERE file_id = f.id) "
            "AND NOT EXISTS (SELECT 1 FROM assets WHERE voice_file_id = f.id)")
        if not orphans:
            return 0
        ids = [f["id"] for f in orphans]
        marks = ", ".join("?" * len(ids))

        def write(c):
            c.execute(f"DELETE FROM wangp_uploads WHERE file_id IN ({marks})", ids)
            c.execute(f"DELETE FROM library_files WHERE id IN ({marks})", ids)
        await self.db.transaction(write)
        for f in orphans:
            for thumb in (False, True):
                if thumb and not f["thumb_rel_path"]:
                    continue
                try:
                    await asyncio.to_thread(self.library.path(f, thumb).unlink, missing_ok=True)
                except Exception:  # noqa: BLE001 - a file already gone or outside the library is just skipped
                    log.warning("couldn't remove library file %s", f["id"], exc_info=True)
        return len(orphans)

    async def _remove_media(self, rows: list[dict]) -> int:
        root, n = self.media_root.resolve(), 0
        for r in rows:
            path = (root / r["rel_path"]).resolve()
            if root in path.parents and path.is_file():
                await asyncio.to_thread(path.unlink)
                n += 1
        return n

    def _remove_dir(self, path: Path):
        root, path = self.media_root.resolve(), path.resolve()
        if root in path.parents and path.is_dir() and path.name != "library":
            shutil.rmtree(path, ignore_errors=True)
