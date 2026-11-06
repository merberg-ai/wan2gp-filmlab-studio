"""Event bus behind the cockpit's /ws stream (schema filmlab.event.v1).

Durable events get a monotonically increasing event_id from the events table
and can be replayed with after_event_id. Ephemeral events (chat token deltas)
are broadcast live only and carry event_id = null.
"""

import asyncio
import json

from .db import Database, now

SCHEMA = "filmlab.event.v1"
MAX_REPLAY = 2000


class EventBus:
    def __init__(self, db: Database):
        self.db = db
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def _broadcast(self, event: dict):
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled client drops its backlog and is told to resync via replay.
                while not q.empty():
                    q.get_nowait()
                q.put_nowait({"schema": SCHEMA, "event_id": None, "type": "stream.overflow", "payload": {}})

    async def publish(self, type_: str, payload: dict, project_id: str | None = None) -> dict:
        ts = now()
        event_id = await self.db.execute(
            "INSERT INTO events (ts, type, project_id, payload) VALUES (?, ?, ?, ?)",
            (ts, type_, project_id, json.dumps(payload)))
        event = _envelope(event_id, type_, project_id, ts, payload)
        self._broadcast(event)
        return event

    def publish_ephemeral(self, type_: str, payload: dict, project_id: str | None = None):
        self._broadcast(_envelope(None, type_, project_id, now(), payload))

    async def replay(self, after_event_id: int) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_event_id, MAX_REPLAY))
        return [_envelope(r["id"], r["type"], r["project_id"], r["ts"], json.loads(r["payload"])) for r in rows]

    async def last_event_id(self) -> int:
        row = await self.db.fetchone("SELECT MAX(id) AS id FROM events")
        return row["id"] or 0


def _envelope(event_id, type_, project_id, ts, payload):
    return {"schema": SCHEMA, "event_id": event_id, "type": type_,
            "project_id": project_id, "timestamp": ts, "payload": payload}
