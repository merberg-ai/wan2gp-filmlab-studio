"""SQLite access: WAL mode, numbered SQL migrations, one serialized writer.

All statements run on a worker thread behind a single lock, so the async
app never blocks the event loop and writes never interleave.
"""

import asyncio
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    def _migrate(self):
        self._conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        done = {r[0] for r in self._conn.execute("SELECT name FROM schema_migrations")}
        pending = [f for f in sorted(MIGRATIONS.glob("*.sql")) if f.name not in done]
        if pending and done and self.path:
            # An existing studio is about to change shape: keep a copy of the database as it was.
            dest = self.path.with_name(f"{self.path.stem}-pre-{pending[0].stem}.sqlite")
            if not dest.exists():
                out = sqlite3.connect(dest)
                with out:
                    self._conn.backup(out)
                out.close()
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            self._conn.execute("BEGIN")
            try:
                for stmt in _split_sql(f.read_text(encoding="utf-8")):
                    self._conn.execute(stmt)
                self._conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (f.name, now()))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _run(self, fn):
        with self._lock:
            return fn(self._conn)

    async def run(self, fn):
        return await asyncio.to_thread(self._run, fn)

    async def execute(self, sql, params=()):
        return await self.run(lambda c: c.execute(sql, params).lastrowid)

    async def fetchone(self, sql, params=()):
        row = await self.run(lambda c: c.execute(sql, params).fetchone())
        return dict(row) if row else None

    async def fetchall(self, sql, params=()):
        rows = await self.run(lambda c: c.execute(sql, params).fetchall())
        return [dict(r) for r in rows]

    async def fetchall_many(self, *queries):
        """Several SELECTs under one lock hold: no write lands between them (a consistent view)."""
        def read(c):
            return [[dict(r) for r in c.execute(sql, params).fetchall()] for sql, params in queries]
        return await self.run(read)

    async def transaction(self, fn):
        """Run fn(conn) inside BEGIN IMMEDIATE ... COMMIT."""
        def wrapped(c):
            c.execute("BEGIN IMMEDIATE")
            try:
                result = fn(c)
                c.execute("COMMIT")
                return result
            except Exception:
                c.execute("ROLLBACK")
                raise
        return await self.run(wrapped)

    async def kv_get(self, key, default=None):
        row = await self.fetchone("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    async def kv_set(self, key, value):
        await self.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                           (key, json.dumps(value)))

    async def audit(self, actor, action, target=None, detail=None):
        await self.execute("INSERT INTO audit (ts, actor, action, target, detail) VALUES (?, ?, ?, ?, ?)",
                           (now(), actor, action, target, json.dumps(detail) if detail is not None else None))

    def close(self):
        with self._lock:
            self._conn.close()


def _split_sql(text):
    """Split a migration file into statements (no semicolons inside our SQL literals)."""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
