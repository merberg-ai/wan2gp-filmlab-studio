"""The Console: what FilmLab is doing in the background, one line each, for the cockpit's Console drawer and page.

Lines come from three places:
- the event bus: renders and generations (sent, WanGP's status line changing, done, failed, stalled), plan parts,
  WanGP coming and going, take inspections, approvals, the GPU hold, backups;
- the director's tool calls, written by the chat with their full arguments and result (so a refused plan shows
  exactly what was refused, and what FilmLab repaired);
- FilmLab's own log at warning and above.
Each line has a level (info, warn, error), a source (Director, Render, Create, Plan, WanGP, System), an optional
project and details. Lines are kept in the database (the newest KEEP) and broadcast live as console.entry.
"""

import asyncio
import json
import logging

from .db import Database, now

KEEP = 5000
LEVELS = ("info", "warn", "error")
SOURCES = ("Director", "Render", "Create", "Plan", "WanGP", "System")
DETAIL_MAX = 20000


class Console:
    def __init__(self, db: Database, bus):
        self.db, self.bus = db, bus
        self._writes = 0
        self._last_status: dict[str, tuple] = {}  # job -> (status, phase, status_text): progress lines only on change
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self):
        self._loop = asyncio.get_running_loop()
        handler = _LogHandler(self)
        handler.setLevel(logging.WARNING)
        logging.getLogger("filmlab").addHandler(handler)
        self._handler = handler

    def stop(self):
        if getattr(self, "_handler", None):
            logging.getLogger("filmlab").removeHandler(self._handler)

    # --- writing -----------------------------------------------------------------------------------------

    async def add(self, level: str, source: str, message: str, project_id: str | None = None, detail=None) -> dict:
        level = level if level in LEVELS else "info"
        text = " ".join(str(message).split())[:500]
        body = None
        if detail is not None:
            body = detail if isinstance(detail, str) else json.dumps(detail, indent=2, ensure_ascii=False, default=str)
            body = body[:DETAIL_MAX]
        ts = now()
        cid = await self.db.execute(
            "INSERT INTO console_log (ts, level, source, message, project_id, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, level, source, text, project_id, body))
        self._writes += 1
        if self._writes % 200 == 0:
            await self.db.execute("DELETE FROM console_log WHERE id <= ?", (cid - KEEP,))
        entry = {"id": cid, "ts": ts, "level": level, "source": source, "message": text, "project_id": project_id,
                 "has_detail": body is not None}
        self.bus.publish_ephemeral("console.entry", entry, project_id)
        return entry

    def add_soon(self, level: str, source: str, message: str, project_id: str | None = None, detail=None):
        """From code that can't await (the log handler): queued on the event loop."""
        loop = self._loop
        if not loop or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(lambda: loop.create_task(self._safe_add(level, source, message, project_id, detail)))
        except RuntimeError:
            pass

    async def _safe_add(self, *args):
        try:
            await self.add(*args)
        except Exception:  # noqa: BLE001 - the console must never break what it watches
            pass

    # --- reading -----------------------------------------------------------------------------------------

    async def listing(self, before_id: int | None = None, limit: int = 200, level: str | None = None,
                      source: str | None = None, project_id: str | None = None, q: str | None = None) -> list[dict]:
        where, args = ["1 = 1"], []
        if before_id:
            where.append("id < ?"); args.append(before_id)
        if level == "warn":
            where.append("level IN ('warn', 'error')")
        elif level == "error":
            where.append("level = 'error'")
        if source:
            where.append("source = ?"); args.append(source)
        if project_id:
            where.append("project_id = ?"); args.append(project_id)
        if q:
            where.append("(message LIKE ? OR detail LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
        rows = await self.db.fetchall(
            f"SELECT id, ts, level, source, message, project_id, detail IS NOT NULL AS has_detail FROM console_log "
            f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*args, max(1, min(int(limit), 500))))
        for r in rows:
            r["has_detail"] = bool(r["has_detail"])
        return rows

    async def detail(self, entry_id: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM console_log WHERE id = ?", (entry_id,))

    # --- from the event bus ------------------------------------------------------------------------------

    async def on_event(self, event: dict):
        try:
            await self._describe(event)
        except Exception:  # noqa: BLE001
            pass

    async def _shot(self, shot_id) -> str:
        s = shot_id and await self.db.fetchone("SELECT position, title FROM shots WHERE id = ?", (shot_id,))
        return f"shot {s['position']} “{s['title']}”" if s else "a shot"

    async def _asset(self, asset_id) -> str:
        a = asset_id and await self.db.fetchone("SELECT name FROM assets WHERE id = ?", (asset_id,))
        return f"“{a['name']}”" if a else "an asset"

    async def _describe(self, e: dict):
        t, p, pid = e["type"], e.get("payload") or {}, e.get("project_id")
        if t.startswith("render.") and p.get("shot_id"):  # renders of shots
            what = await self._shot(p["shot_id"])
            if p.get("preview"):
                what += " (preview)"
            await self._job_line(t, p, pid, "Render", what)
        elif t.startswith(("image.", "render.")) and p.get("asset_id"):  # pictures, voices, songs, test clips
            await self._job_line(t, p, pid, "Create", f"generation for {await self._asset(p['asset_id'])}")
        elif t == "plan.progress":
            await self.add("info", "Plan", f"Plan part {p.get('parts')} saved: {p.get('scenes')} scene(s), "
                                           f"{p.get('seconds')} s of film so far", pid)
        elif t == "wangp.status":
            await self.add("info" if p.get("connected") else "warn", "WanGP",
                           "WanGP is back; waiting work carries on" if p.get("connected")
                           else "WanGP stopped answering; new renders wait for it", pid)
        elif t == "chat.message" and p.get("role") == "error":
            await self.add("error", "Director", p.get("content") or "the director's turn failed", pid, p.get("content"))
        elif t == "chat.message" and p.get("role") == "note":
            await self.add("info", "Director", p.get("content") or "", pid)
        elif t == "take.inspected":
            row = await self.db.fetchone("SELECT verdict, summary FROM take_inspections WHERE take_id = ?", (p.get("take_id"),))
            if row:
                lvl = "warn" if row["verdict"] in ("check", "bad", "fail") else "info"
                await self.add(lvl, "Render", f"Inspected the take of {await self._shot(p.get('shot_id'))}: "
                                              f"{row['verdict']}. {row['summary'] or ''}", pid)
        elif t == "approval.created":
            await self.add("info", "Director", f"The director asks to render {await self._shot(p.get('shot_id'))}", pid)
        elif t == "gpu.hold":
            await self.add("warn" if p.get("on") else "info", "System",
                           "GPU work is on hold" if p.get("on") else "GPU hold released", pid)
        elif t == "backup.updated" and p.get("name"):
            await self.add("info", "System", f"Backup {p['name']} updated", pid)

    async def _job_line(self, t: str, p: dict, pid, source: str, what: str):
        job = p.get("job_id") or ""
        if t.endswith(".submitted"):
            await self.add("info", source, f"Sent {what} to WanGP", pid)
        elif t.endswith(".done"):
            self._last_status.pop(job, None)
            await self.add("info", source, f"Done: {what}", pid)
        elif t.endswith(".ended"):
            self._last_status.pop(job, None)
            status = p.get("status") or "ended"
            lvl = "info" if status == "cancelled" else "error"
            info = p.get("error_info") or {}
            msg = f"{'Cancelled' if status == 'cancelled' else 'Failed'}: {what}"
            if status != "cancelled" and (info.get("message") or p.get("error")):
                msg += f". {info.get('message') or ''} {info.get('hint') or ''}".rstrip()
            await self.add(lvl, source, msg, pid, p.get("error"))
        elif t.endswith(".stalled"):
            await self.add("warn", source, f"No progress from WanGP for a while on {what}", pid)
        elif t.endswith(".progress"):
            key = (p.get("status"), p.get("phase"), p.get("status_text"))
            if self._last_status.get(job) == key:
                return  # steps ticking: not news
            self._last_status[job] = key
            if p.get("status_text"):
                await self.add("info", "WanGP", f"{what}: {p['status_text']}", pid)
            elif p.get("phase"):
                await self.add("info", source, f"{what}: {str(p['phase']).replace('_', ' ')}", pid)
            elif p.get("queue_ahead"):
                await self.add("info", source, f"{what}: waiting in WanGP's queue, {p['queue_ahead']} ahead", pid)


class _LogHandler(logging.Handler):
    """FilmLab's warnings and errors, as System lines (with the traceback as details)."""

    def __init__(self, console: Console):
        super().__init__()
        self.console = console

    def emit(self, record: logging.LogRecord):
        if record.name.startswith("filmlab.console"):
            return
        try:
            msg = record.getMessage()
            detail = self.format(record) if record.exc_info else None
            level = "error" if record.levelno >= logging.ERROR else "warn"
            self.console.add_soon(level, "System", f"{record.name.removeprefix('filmlab.') or 'filmlab'}: {msg}", None, detail)
        except Exception:  # noqa: BLE001
            pass
