"""Studio health: database integrity, files, storage, lost jobs, GPU sharing and startup state.

Everything here reads. The only changes it makes are the owner's explicit cleanup actions: clearing caches
that regenerate by themselves, and removing files on disk that no database row refers to (listed first).
GPU sharing only *reads* Ollama's list of loaded models; FilmLab never unloads or stops another app's models.
"""

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path

import httpx

from .db import Database, now

log = logging.getLogger("filmlab.health")
DEFAULTS = {"warn_free_gb": 20, "min_free_gb": 5, "gpu_share": "warn", "ollama_url": "http://127.0.0.1:11434"}
ACTIVE = ("submitting", "queued", "running", "cancelling", "restored")
UNITS = ("filmlab-backend", "filmlab-gateway")


class HealthError(ValueError):
    pass


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


class Health:
    def __init__(self, db: Database, media_dir: Path, data_dir: Path, backup_dir: Path,
                 ollama_transport: httpx.AsyncBaseTransport | None = None):
        self.db, self.media, self.data, self.backups = db, media_dir, data_dir, backup_dir
        self._transport = ollama_transport
        self.wangp = None  # set by Studio: (client, agent_context) for lost-job reconciliation

    async def settings(self) -> dict:
        return DEFAULTS | (await self.db.kv_get("health.settings") or {})

    async def set_settings(self, patch: dict) -> dict:
        cur = await self.settings()
        for k in ("warn_free_gb", "min_free_gb"):
            if k in patch:
                v = int(patch[k])
                if not 0 <= v <= 2000:
                    raise HealthError(f"{k} must be 0-2000")
                cur[k] = v
        if cur["min_free_gb"] > cur["warn_free_gb"]:
            raise HealthError("the refuse level must not be above the warning level")
        if "gpu_share" in patch:
            if patch["gpu_share"] not in ("off", "warn", "wait"):
                raise HealthError("gpu_share must be off, warn or wait")
            cur["gpu_share"] = patch["gpu_share"]
        await self.db.kv_set("health.settings", {k: cur[k] for k in ("warn_free_gb", "min_free_gb", "gpu_share")})
        return cur

    # --- gates used before GPU work ----------------------------------------------

    async def free_gb(self) -> float:
        return shutil.disk_usage(self.media).free / 1e9

    async def storage_block(self) -> str | None:
        cfg = await self.settings()
        free = await self.free_gb()
        if free < cfg["min_free_gb"]:
            return (f"only {free:.1f} GB free on the FilmLab disk (renders need at least {cfg['min_free_gb']} GB); "
                    "free space in Health → Storage")
        return None

    async def ollama_loaded(self) -> list[dict]:
        """Local models Ollama holds in memory (cloud models don't use this GPU). Read-only."""
        cfg = await self.settings()
        try:
            async with httpx.AsyncClient(base_url=cfg["ollama_url"], timeout=3, transport=self._transport) as c:
                r = await c.get("/api/ps")
                r.raise_for_status()
                models = r.json().get("models") or []
        except (httpx.HTTPError, ValueError):
            return []
        return [{"name": m.get("name"), "vram_gb": round((m.get("size_vram") or 0) / 1e9, 1)}
                for m in models if (m.get("size_vram") or 0) > 0 and not str(m.get("name", "")).endswith("-cloud")]

    async def gpu_share_block(self) -> str | None:
        """With gpu_share = wait: hold new GPU work while a local Ollama model (another app's, say) holds VRAM."""
        if (await self.settings())["gpu_share"] != "wait":
            return None
        loaded = await self.ollama_loaded()
        if loaded:
            names = ", ".join(f"{m['name']} ({m['vram_gb']} GB)" for m in loaded)
            return f"waiting: Ollama has {names} loaded on the GPU; FilmLab renders when it's unloaded"
        return None

    async def gpu_share_note(self) -> str | None:
        cfg = await self.settings()
        if cfg["gpu_share"] == "off":
            return None
        loaded = await self.ollama_loaded()
        if not loaded:
            return None
        names = ", ".join(f"{m['name']} ({m['vram_gb']} GB)" for m in loaded)
        return f"Ollama has {names} loaded on the GPU; a render now shares VRAM with it and may run out of memory"

    # --- the report -------------------------------------------------------------------

    async def report(self) -> dict:
        integrity, fks = await self.db.run(lambda c: (c.execute("PRAGMA quick_check").fetchone()[0],
                                                      [tuple(r) for r in c.execute("PRAGMA foreign_key_check").fetchall()]))
        files = await self.files()
        storage = await asyncio.to_thread(self._storage)
        cfg = await self.settings()
        free = storage["free_gb"]
        jobs = await self.jobs()
        loaded = await self.ollama_loaded()
        problems = []
        if integrity != "ok":
            problems.append(f"database check: {integrity}")
        if fks:
            problems.append(f"{len(fks)} broken database reference(s)")
        if files["missing"]:
            problems.append(f"{len(files['missing'])} file(s) the database needs are missing")
        if free < cfg["warn_free_gb"]:
            problems.append(f"only {free:.1f} GB free on the FilmLab disk")
        if jobs["stale"] or jobs["unknown_in_wangp"]:
            problems.append("job bookkeeping and WanGP's queue disagree (see Jobs)")
        return {"at": now(), "ok": not problems, "problems": problems,
                "database": {"integrity": integrity, "foreign_key_problems": len(fks)},
                "files": {k: (v[:50] if isinstance(v, list) else v) for k, v in files.items() if k != "deleted_delivery_files"},
                "storage": storage, "jobs": jobs, "startup": await asyncio.to_thread(self._startup),
                "gpu": {"ollama_loaded": loaded, "share": cfg["gpu_share"], "vram": await asyncio.to_thread(self._vram)},
                "settings": cfg}

    async def files(self) -> dict:
        def referenced(c):
            refs = {r[0] for r in c.execute("SELECT rel_path FROM media")}
            refs |= {f"library/{r[0]}" for r in c.execute("SELECT rel_path FROM library_files")}
            refs |= {f"library/{r[0]}" for r in c.execute("SELECT thumb_rel_path FROM library_files WHERE thumb_rel_path IS NOT NULL")}
            dirs = {r[0] for r in c.execute("SELECT rel_dir FROM exports WHERE deleted_at IS NULL AND rel_dir IS NOT NULL")}
            gone = {r[0] for r in c.execute("SELECT rel_dir FROM exports WHERE deleted_at IS NOT NULL AND rel_dir IS NOT NULL")}
            return refs, dirs, gone
        refs, export_dirs, deleted_dirs = await self.db.run(referenced)

        def scan():
            missing = sorted(r for r in refs if not (self.media / r).is_file())
            orphans, orphan_bytes, deleted_files, deleted_bytes = [], 0, [], 0
            for p in self.media.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(self.media).as_posix()
                if rel.startswith("cache/") or rel in refs or any(rel.startswith(d.rstrip("/") + "/") for d in export_dirs):
                    continue
                if "/exports/" in rel and rel.split("/")[-1] in ("film.mp4.part",):
                    continue  # an export being written right now
                if any(rel.startswith(d.rstrip("/") + "/") for d in deleted_dirs):
                    deleted_files.append(rel)  # a delivery the owner deleted: its files wait for "Remove"
                    deleted_bytes += p.stat().st_size
                    continue
                orphans.append(rel)
                orphan_bytes += p.stat().st_size
            return {"referenced": len(refs), "missing": missing, "orphans": sorted(orphans), "orphan_bytes": orphan_bytes,
                    "deleted_deliveries": len({f.rsplit("/", 1)[0] for f in deleted_files}),
                    "deleted_delivery_bytes": deleted_bytes, "deleted_delivery_files": sorted(deleted_files)}
        return await asyncio.to_thread(scan)

    def _storage(self) -> dict:
        du = shutil.disk_usage(self.media)
        kinds = {"library": self.media / "library", "caches": self.media / "cache"}
        sizes = {k: _size(p) if p.exists() else 0 for k, p in kinds.items()}
        takes = exports = 0
        for proj in self.media.iterdir() if self.media.exists() else []:
            if proj.is_dir() and proj.name not in ("library", "cache"):
                for f in proj.rglob("*"):
                    if f.is_file():
                        if "/exports/" in f.as_posix():
                            exports += f.stat().st_size
                        else:
                            takes += f.stat().st_size
        backups = _size(self.backups) if self.backups.exists() else 0  # hard links counted once per backup
        return {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1),
                "takes_mb": round(takes / 1e6, 3), "deliveries_mb": round(exports / 1e6, 3),
                "library_mb": round(sizes["library"] / 1e6, 1), "caches_mb": round(sizes["caches"] / 1e6, 1),
                "database_mb": round(_size(self.data) / 1e6, 1), "backups_mb": round(backups / 1e6, 1)}

    async def jobs(self) -> dict:
        active = await self.db.fetchall(
            f"SELECT 'render' AS kind, id, wangp_job_id, status, submitted_at FROM render_jobs WHERE status IN {ACTIVE} "
            f"UNION ALL SELECT 'image', id, wangp_job_id, status, submitted_at FROM image_jobs WHERE status IN {ACTIVE}")
        out = {"active": len(active), "stale": [], "unknown_in_wangp": [], "checked": False}
        if not self.wangp:
            return out
        client, agent = self.wangp
        agent = agent or getattr(client, "agent_context", None)  # known once WanGP is connected
        try:
            queue = await client.session("list_queue")
        except Exception as exc:  # noqa: BLE001
            return out | {"error": f"WanGP queue not readable: {str(exc)[:200]}"}
        tasks = queue.get("tasks", [])
        ours = [t for t in tasks if agent and t.get("mcp_agent_id") == agent]
        known = {j["wangp_job_id"] for j in active if j["wangp_job_id"]}
        listed = {(t.get("client_id") or "").split("_")[1] for t in ours if (t.get("client_id") or "").count("_") >= 2}
        for j in active:
            if j["wangp_job_id"] and j["wangp_job_id"] not in listed and j["status"] in ("queued", "restored"):
                out["stale"].append({k: j[k] for k in ("kind", "id", "status", "submitted_at")})
        for t in ours:
            cid = t.get("client_id") or ""
            jid = cid.split("_")[1] if cid.count("_") >= 2 else None
            if jid and jid not in known:
                out["unknown_in_wangp"].append({"client_id": cid, "status": t.get("status")})
        return out | {"checked": True, "wangp_tasks": len(tasks), "filmlab_tasks": len(ours)}

    def _startup(self) -> dict:
        units = {}
        for u in UNITS:
            try:
                active = subprocess.run(["systemctl", "--user", "is-active", u], capture_output=True, text=True,
                                        timeout=5).stdout.strip()
                enabled = subprocess.run(["systemctl", "--user", "is-enabled", u], capture_output=True, text=True,
                                         timeout=5).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                active, enabled = "unknown", "unknown"
            units[u] = {"active": active, "enabled_at_boot": enabled in ("enabled", "enabled-runtime", "linked")}
        return {"units": units, "manual": not any(u["enabled_at_boot"] for u in units.values())}

    def _vram(self) -> dict | None:
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
            name, used, total = [x.strip() for x in out[0].split(",")]
            return {"gpu": name, "used_gb": round(int(used) / 1024, 1), "total_gb": round(int(total) / 1024, 1)}
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return None

    # --- cleanup (owner actions) ----------------------------------------------------

    async def clear_caches(self, actor: str) -> dict:
        """Waveforms, filmstrips and inspection sheets: all rebuilt on demand. Inspections keep their results."""
        cache = self.media / "cache"
        freed = await asyncio.to_thread(lambda: sum(_size(cache / d) for d in ("waveforms", "filmstrips") if (cache / d).exists()))
        for sub in ("waveforms", "filmstrips"):
            await asyncio.to_thread(shutil.rmtree, cache / sub, True)
        await self.db.audit(actor, "health.clear_caches", None, {"bytes": freed})
        return {"freed_mb": round(freed / 1e6, 1)}

    async def remove_orphans(self, listed: list[str], actor: str) -> dict:
        """Remove exactly the files the owner saw listed, if they are still orphans."""
        now_orphans = set((await self.files())["orphans"])
        root = self.media.resolve()
        removed, freed = 0, 0
        for rel in listed:
            if rel not in now_orphans:
                continue
            p = (root / rel).resolve()
            if root in p.parents and p.is_file():
                freed += p.stat().st_size
                await asyncio.to_thread(p.unlink)
                removed += 1
        await self.db.audit(actor, "health.remove_orphans", None, {"files": removed, "bytes": freed})
        return {"removed": removed, "freed_mb": round(freed / 1e6, 1)}

    async def remove_deleted_deliveries(self, actor: str) -> dict:
        """Files of deliveries the owner deleted (their rows stay, marked deleted, for the record)."""
        dirs = [r["rel_dir"] for r in await self.db.fetchall(
            "SELECT rel_dir FROM exports WHERE deleted_at IS NOT NULL AND rel_dir IS NOT NULL")]
        root = self.media.resolve()
        removed, freed = 0, 0
        for d in dirs:
            p = (root / d).resolve()
            if root in p.parents and p.is_dir() and "exports" in p.parts:
                freed += _size(p)
                await asyncio.to_thread(shutil.rmtree, p, True)
                removed += 1
        await self.db.audit(actor, "health.remove_deleted_deliveries", None, {"deliveries": removed, "bytes": freed})
        return {"removed": removed, "freed_mb": round(freed / 1e6, 1)}

    @staticmethod
    def as_json(report: dict) -> str:
        return json.dumps(report, indent=1)
