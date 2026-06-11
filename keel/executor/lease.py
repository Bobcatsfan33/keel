"""Worker leasing.

A run is fully described by its event log, so "parked" and "crashed" are
indistinguishable to a fresh worker: it folds the log, sees the frontier, and
continues. Leasing is the cooperative lock that keeps *two* workers from advancing
the same run at once. Combined with the store's ``(run_id, seq)`` uniqueness, even a
brief double-lease during a partition cannot corrupt the log — the loser's append
collides on seq and is rejected.

The protocol has an in-memory implementation (single-process clusters / tests) and a
Postgres advisory-lock implementation (`substrate.store.postgres`) for real
multi-node deployments. Both sit behind ``LeaseManager`` so the worker loop is
identical against either.
"""
from __future__ import annotations
from typing import Optional, Protocol, runtime_checkable
from ..substrate.ports import Clock

DEFAULT_TTL_S = 30.0


@runtime_checkable
class LeaseManager(Protocol):
    async def acquire(self, run_id: str, worker_id: str) -> bool: ...
    async def heartbeat(self, run_id: str, worker_id: str) -> bool: ...
    async def release(self, run_id: str, worker_id: str) -> None: ...


class MemoryLeaseManager:
    """In-memory cooperative lease with TTL-based stealing. A lease whose deadline
    has passed may be stolen by any worker (its original holder is presumed dead)."""

    def __init__(self, clock: Clock, ttl_s: float = DEFAULT_TTL_S) -> None:
        self._clock = clock
        self._ttl = ttl_s
        self._leases: dict[str, tuple[str, float]] = {}  # run_id -> (worker, expires)

    async def acquire(self, run_id: str, worker_id: str) -> bool:
        now = self._clock.monotonic()
        cur = self._leases.get(run_id)
        if cur is not None and cur[1] > now and cur[0] != worker_id:
            return False  # held and still live by another worker
        self._leases[run_id] = (worker_id, now + self._ttl)
        return True

    async def heartbeat(self, run_id: str, worker_id: str) -> bool:
        cur = self._leases.get(run_id)
        if cur is None or cur[0] != worker_id:
            return False
        self._leases[run_id] = (worker_id, self._clock.monotonic() + self._ttl)
        return True

    async def release(self, run_id: str, worker_id: str) -> None:
        cur = self._leases.get(run_id)
        if cur is not None and cur[0] == worker_id:
            del self._leases[run_id]

    def holder(self, run_id: str) -> Optional[str]:
        cur = self._leases.get(run_id)
        if cur is None or cur[1] <= self._clock.monotonic():
            return None
        return cur[0]
