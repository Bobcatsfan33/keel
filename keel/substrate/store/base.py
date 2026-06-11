from __future__ import annotations
from typing import Protocol, AsyncIterator, runtime_checkable
from ..events import Event


class DuplicateEventError(Exception):
    """Raised when an (run_id, seq) collision is detected — a replayed/duplicate
    write. The executor treats this as 'already persisted' and continues."""


@runtime_checkable
class EventStore(Protocol):
    async def append_batch(self, events: list[Event]) -> None: ...
    def read_run(self, run_id: str) -> AsyncIterator[Event]: ...
    async def list_runs(self, limit: int = 100) -> list[str]: ...
