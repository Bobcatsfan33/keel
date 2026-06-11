from __future__ import annotations
import aiosqlite
from typing import AsyncIterator
from ..events import Event
from .base import DuplicateEventError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    run_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    event_id   TEXT NOT NULL,
    ts         TEXT NOT NULL,
    type       TEXT NOT NULL,
    node_id    TEXT,
    body       TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
"""


class SqliteEventStore:
    def __init__(self, path: str = "keel.db") -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> "SqliteEventStore":
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(_SCHEMA)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.commit()
        return self

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()

    async def append_batch(self, events: list[Event]) -> None:
        assert self._db is not None
        rows = [
            (e.run_id, e.seq, e.event_id, e.ts.isoformat(), e.type.value, e.node_id, e.to_json())
            for e in events
        ]
        try:
            await self._db.executemany(
                "INSERT INTO events(run_id, seq, event_id, ts, type, node_id, body)"
                " VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            await self._db.commit()
        except aiosqlite.IntegrityError as e:
            await self._db.rollback()
            raise DuplicateEventError(str(e)) from e

    async def read_run(self, run_id: str) -> AsyncIterator[Event]:
        assert self._db is not None
        async with self._db.execute(
            "SELECT body FROM events WHERE run_id=? ORDER BY seq ASC", (run_id,)
        ) as cur:
            async for (body,) in cur:
                yield Event.from_json(body)

    async def list_runs(self, limit: int = 100) -> list[str]:
        assert self._db is not None
        async with self._db.execute(
            "SELECT DISTINCT run_id FROM events ORDER BY run_id DESC LIMIT ?", (limit,)
        ) as cur:
            return [r[0] async for r in cur]
