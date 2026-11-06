"""Backups of the whole studio: the database, every media and library file, and a manifest.

A backup is a folder under backup_dir (default ~/filmlab/backups) named <UTC time>-<kind>:
  filmlab.sqlite   an online SQLite backup (consistent while the backend runs)
  media/           the media folder without its regenerable caches, copied with rsync; files unchanged since
                   the previous backup are hard links to it, so a daily backup costs only what changed
  manifest.json    when, which app version and migrations, row counts, and every file with size and SHA-256
Secrets (secrets.env) are never copied. Restoring is deliberately not a cockpit action: scripts/restore.sh
does it with the backend stopped and keeps the current data aside. The cockpit can *verify* a backup and run
a restore drill, which proves from the backup alone that a project's delivery can be rebuilt frame for frame.
"""

import asyncio
import json
import logging
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import VERSION
from .db import Database, now
from .events import EventBus
from .media import sha256_file

log = logging.getLogger("filmlab.backups")
DEFAULTS = {"daily": True, "keep": 10, "copy_dir": ""}
COUNTED = ("projects", "shots", "takes", "media", "library_files", "assets", "exports")
EXCLUDE = ("cache/",)


class BackupError(ValueError):
    pass


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


async def _run(*cmd) -> str:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise BackupError(f"{cmd[0]} failed: {err.decode(errors='replace')[-400:]}")
    return out.decode(errors="replace")


class Backups:
    def __init__(self, db: Database, bus: EventBus, db_path: Path, media_dir: Path, backup_dir: Path):
        self.db, self.bus, self.db_path, self.media_dir, self.root = db, bus, db_path, media_dir, backup_dir
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task | None = None
        self.exporter = None  # set by Studio: the restore drill rebuilds a delivery with its commands

    # --- settings -----------------------------------------------------------------

    async def settings(self) -> dict:
        return DEFAULTS | (await self.db.kv_get("backups.settings") or {})

    async def set_settings(self, patch: dict) -> dict:
        cur = await self.settings()
        if "daily" in patch:
            cur["daily"] = bool(patch["daily"])
        if "keep" in patch:
            keep = int(patch["keep"])
            if not 1 <= keep <= 100:
                raise BackupError("keep must be 1-100")
            cur["keep"] = keep
        if "copy_dir" in patch:
            d = str(patch["copy_dir"] or "").strip()
            if d and (not (d.startswith("/mnt/") or d.startswith("/home/")) or ".." in Path(d).parts
                      or d.lower().startswith(("/mnt/c/windows", "/mnt/c/program files"))):
                raise BackupError("copy_dir must be a folder path inside the distro, such as /mnt/d/FilmLabBackups")
            if d and any(Path(d).resolve().is_relative_to(p.resolve()) or p.resolve().is_relative_to(Path(d).resolve())
                         for p in (self.root, self.media_dir, self.db_path.parent)):
                raise BackupError("copy_dir can't be inside (or contain) FilmLab's own data, media or backups")
            cur["copy_dir"] = d
        await self.db.kv_set("backups.settings", cur)
        return cur

    # --- listing ------------------------------------------------------------------

    async def list(self) -> list[dict]:
        out = []
        if not self.root.is_dir():
            return out
        for d in sorted(self.root.iterdir(), reverse=True):
            m = d / "manifest.json"
            if d.is_dir() and m.is_file():
                try:
                    man = json.loads(m.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                out.append({k: man.get(k) for k in ("name", "kind", "created_at", "version", "counts", "bytes", "files",
                                                    "new_bytes", "verified", "drill", "note")}
                           | {"files": len(man.get("files") or [])})
        return out

    def path(self, name: str) -> Path:
        p = (self.root / name).resolve()
        if p.parent != self.root.resolve() or not (p / "manifest.json").is_file():
            raise BackupError("no such backup")
        return p

    # --- create -------------------------------------------------------------------

    async def create(self, kind: str = "manual", note: str = "", actor: str = "owner") -> dict:
        if kind not in ("manual", "daily", "pre-update"):
            raise BackupError("unknown backup kind")
        async with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            name, n = f"{_stamp()}-{kind}", 1
            while (self.root / name).exists():  # two in the same second
                n += 1
                name = f"{_stamp()}-{kind}-{n}"
            work = self.root / f".{name}.part"
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir()
            try:
                await asyncio.to_thread(self._db_snapshot, work / "filmlab.sqlite")
                previous = next((b for b in await self.list()), None)
                args = ["rsync", "-a", "--delete", *[f"--exclude={e}" for e in EXCLUDE]]
                if previous:
                    args.append(f"--link-dest={self.root / previous['name'] / 'media'}")
                (work / "media").mkdir()
                await _run(*args, f"{self.media_dir}/", f"{work / 'media'}/")
                manifest = await asyncio.to_thread(self._manifest, work, name, kind, note)
                (work / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
                work.rename(self.root / name)
            except Exception:
                shutil.rmtree(work, ignore_errors=True)
                raise
            await self.db.audit(actor, "backup.create", name, {"kind": kind, "files": len(manifest["files"]),
                                                                "bytes": manifest["bytes"]})
            await self._prune()
            copied = await self._copy_out(name)
            await self.bus.publish("backup.updated", {"name": name})
            return {k: manifest[k] for k in ("name", "kind", "created_at", "bytes", "new_bytes", "counts")} | {
                "files": len(manifest["files"]), "copied_to": copied}

    def _db_snapshot(self, dest: Path):
        src = sqlite3.connect(self.db_path)
        try:
            out = sqlite3.connect(dest)
            with out:
                src.backup(out)
            with out:  # login sessions are not data: a restored studio asks for the password again
                out.execute("DELETE FROM sessions")
            out.execute("VACUUM")  # so the deleted rows don't linger in free pages
            out.close()
        finally:
            src.close()

    def _manifest(self, work: Path, name: str, kind: str, note: str) -> dict:
        c = sqlite3.connect(work / "filmlab.sqlite")
        try:
            counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in COUNTED}
            migrations = [r[0] for r in c.execute("SELECT name FROM schema_migrations ORDER BY name")]
            known = {r[0]: r[1] for r in c.execute("SELECT rel_path, sha256 FROM media")}
            known |= {f"library/{r[0]}": r[1] for r in c.execute("SELECT rel_path, sha256 FROM library_files")}
        finally:
            c.close()
        files, total, new = [], 0, 0
        media = work / "media"
        for p in sorted(media.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(media))
            st = p.stat()
            total += st.st_size
            if st.st_nlink == 1:
                new += st.st_size
            # Recorded hashes come from the database (computed when the file arrived); deep verify rehashes.
            files.append({"rel": rel, "bytes": st.st_size, "sha256": known.get(rel)})
        return {"name": name, "kind": kind, "note": note[:200], "created_at": now(), "version": VERSION,
                "migrations": migrations, "counts": counts, "files": files, "bytes": total, "new_bytes": new}

    async def _prune(self):
        keep = (await self.settings())["keep"]
        auto = [b for b in await self.list() if b["kind"] in ("daily", "pre-update")]
        for b in auto[keep:]:
            await asyncio.to_thread(shutil.rmtree, self.root / b["name"], True)
            log.info("backup %s pruned (keeping %s automatic backups)", b["name"], keep)

    async def _copy_out(self, name: str) -> str | None:
        target = (await self.settings())["copy_dir"]
        if not target:
            return None
        dest = Path(target)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            await _run("rsync", "-a", f"{self.root / name}/", f"{dest / name}/")
            return str(dest / name)
        except Exception as exc:  # noqa: BLE001 - the local backup stands; the copy is reported
            log.warning("copying backup %s to %s failed: %s", name, dest, exc)
            await self.db.audit("system", "backup.copy_failed", name, {"error": str(exc)[:300]})
            return None

    async def delete(self, name: str, actor: str) -> dict:
        p = self.path(name)
        await asyncio.to_thread(shutil.rmtree, p)
        await self.db.audit(actor, "backup.delete", name)
        await self.bus.publish("backup.updated", {"name": name})
        return {"deleted": name}

    # --- verify and drill -------------------------------------------------------

    async def verify(self, name: str, deep: bool = False) -> dict:
        p = self.path(name)
        man = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
        problems = []

        def check():
            c = sqlite3.connect(f"file:{p / 'filmlab.sqlite'}?mode=ro", uri=True)
            try:
                ok = c.execute("PRAGMA integrity_check").fetchone()[0]
                if ok != "ok":
                    problems.append(f"database: {ok}")
                rows = [r[0] for r in c.execute("SELECT rel_path FROM media")] + \
                    [f"library/{r[0]}" for r in c.execute("SELECT rel_path FROM library_files")]
            finally:
                c.close()
            listed = {f["rel"]: f for f in man["files"]}
            for rel in rows:
                if rel not in listed:
                    problems.append(f"missing from the backup: {rel}")
            hashed = 0
            for f in man["files"]:
                fp = p / "media" / f["rel"]
                if not fp.is_file():
                    problems.append(f"file gone: {f['rel']}")
                elif fp.stat().st_size != f["bytes"]:
                    problems.append(f"size changed: {f['rel']}")
                elif deep and f.get("sha256"):
                    hashed += 1
                    if sha256_file(fp) != f["sha256"]:
                        problems.append(f"content changed: {f['rel']}")
            return hashed
        hashed = await asyncio.to_thread(check)
        result = {"ok": not problems, "deep": deep, "checked_files": len(man["files"]), "hashed": hashed,
                  "problems": problems[:50], "at": now()}
        man["verified"] = result
        (p / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
        await self.db.audit("owner", "backup.verify", name, {"ok": result["ok"], "problems": len(problems)})
        return result

    async def drill(self, name: str) -> dict:
        """Non-destructive restore drill: from the backup alone, rebuild a delivery and compare it frame by frame
        with the backup's copy of that delivery. Nothing in the live studio is read or changed."""
        p = self.path(name)
        from .export import build_command, copy_command  # here: export imports media helpers, not us
        exp = self.exporter

        def pick():
            c = sqlite3.connect(f"file:{p / 'filmlab.sqlite'}?mode=ro", uri=True)
            c.row_factory = sqlite3.Row
            try:
                migrations = [r[0] for r in c.execute("SELECT name FROM schema_migrations")]
                row = c.execute("SELECT e.id, e.rel_dir, e.manifest, e.sha256, e.number, pr.title FROM exports e "
                                "JOIN projects pr ON pr.id = e.project_id WHERE e.kind = 'final' AND e.status = 'done' "
                                "AND e.deleted_at IS NULL ORDER BY e.finished_at DESC LIMIT 1").fetchone()
                return migrations, dict(row) if row else None
            finally:
                c.close()
        migrations, delivery = await asyncio.to_thread(pick)
        here = sorted(f.name for f in (Path(__file__).parent / "migrations").glob("*.sql"))
        result = {"at": now(), "schema": "current" if sorted(migrations) == here else
                  f"older: {len(migrations)} of {len(here)} migrations (they apply on restore)"}
        if not delivery:
            result |= {"ok": True, "detail": "no delivery in this backup to rebuild; database and files checked only"}
        else:
            m = json.loads(delivery["manifest"])
            media = p / "media"
            film = media / delivery["rel_dir"] / "film.mp4"
            with tempfile.TemporaryDirectory(prefix="filmlab-drill-") as tmp:
                work, out = Path(tmp), Path(tmp) / "rebuilt.mp4"
                cmd = (copy_command(m, media, work, out) if m["mode"] == "copy"
                       else build_command(m, media, media / "library", work, out))
                await _run(*cmd)
                a = await exp._framehashes(film)
                b = await exp._framehashes(out)
            same = a == b
            result |= {"ok": same and film.is_file(), "project": delivery["title"], "delivery": delivery["number"],
                       "detail": (f"Delivery {delivery['number']} of “{delivery['title']}” rebuilt from the backup alone: "
                                  f"{len(a)} frames and audio packets {'identical' if same else 'DIFFER'}")}
        man = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
        man["drill"] = result
        (p / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
        await self.db.audit("owner", "backup.drill", name, {"ok": result["ok"]})
        return result

    # --- daily ------------------------------------------------------------------------

    def start_daily(self):
        self._timer = asyncio.create_task(self._daily())

    async def _daily(self):
        await asyncio.sleep(120)  # let startup and recovery finish first
        while True:
            try:
                cfg = await self.settings()
                latest = next((b for b in await self.list()), None)
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
                       ).total_seconds() if latest else None
                if cfg["daily"] and (age is None or age > 86400):
                    await self.create("daily", actor="system")
            except Exception:  # noqa: BLE001 - logged and audited; the next hour tries again
                log.exception("daily backup failed")
                await self.db.audit("system", "backup.failed", None)
            await asyncio.sleep(3600)

    async def shutdown(self):
        if self._timer:
            self._timer.cancel()
            await asyncio.gather(self._timer, return_exceptions=True)
