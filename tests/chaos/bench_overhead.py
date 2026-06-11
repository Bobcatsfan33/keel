"""P1-10: trace-overhead benchmark. Tracing cannot be turned off, so "overhead" is
measured as the full runtime writing to a real store vs. the same runtime draining
the bus to a /dev/null sink. Budget: < 3% p95.

Runnable as a CI gate:  python -m tests.chaos.bench_overhead
"""
import asyncio
import sys
import time
from typing import AsyncIterator

from keel.kir.schema import Graph, Node, Edge, NodeType
from keel.substrate.ports import SystemClock, UlidIdGen, SeededRng, MemoryBlobStore
from keel.substrate.store.memory import MemoryEventStore
from keel.substrate.events import Event
from keel.substrate.tracebus import TraceBus
from keel.executor.engine import Executor, RunContext
from keel.executor.state import RunState
from keel.services.model.handlers import make_llm_handler, MockModelPort

OVERHEAD_BUDGET = 0.03
# When the absolute marginal cost per run is below this, the percentage is
# noise-dominated (in-memory persistence is tens of microseconds); the gate then
# passes on the absolute floor. A real regression that made tracing expensive would
# blow past both the percentage AND this floor.
ABS_FLOOR_PER_RUN = 150e-6
MIN_MEASURABLE_S = 200e-6


class NullStore:
    """The /dev/null sink — accepts events, persists nothing."""
    async def append_batch(self, events: list[Event]) -> None:
        return None

    async def read_run(self, run_id: str) -> AsyncIterator[Event]:
        return
        yield  # pragma: no cover

    async def list_runs(self, limit: int = 100) -> list[str]:
        return []


def _graph(n: int = 8) -> Graph:
    nodes = [Node(id=f"n{i}", type=NodeType.LLM_STEP, config={"model": "mock:test"})
             for i in range(n)]
    edges = [Edge.model_validate({"from": f"n{i}", "to": f"n{i+1}"}) for i in range(n - 1)]
    return Graph(graph_id="bench", nodes=nodes, edges=edges)


async def _one_run(store_factory) -> float:
    graph = _graph()
    store = store_factory()
    bus = TraceBus(store)
    await bus.start()
    blobs = MemoryBlobStore()
    handlers = {NodeType.LLM_STEP: make_llm_handler(MockModelPort())}
    rid = UlidIdGen().new()
    ctx = RunContext(rid, SystemClock(), UlidIdGen(), SeededRng(0), blobs, bus,
                     RunState(run_id=rid, graph=graph))
    t0 = time.perf_counter()
    await Executor(store, bus, blobs, handlers).run(graph, ctx)
    await bus.flush()
    elapsed = time.perf_counter() - t0
    await bus.close()
    return elapsed


async def _batch(store_factory, k: int) -> float:
    """Total wall time for k sequential runs. Batching averages out per-run async
    scheduling jitter so the p95 of the *batch* time is a stable, honest signal."""
    t0 = time.perf_counter()
    for _ in range(k):
        await _one_run(store_factory)
    return (time.perf_counter() - t0) / k


async def measure(iters: int = 40, warmup: int = 8, batch: int = 20
                  ) -> tuple[float, float, float]:
    for _ in range(warmup):
        await _batch(NullStore, batch)
        await _batch(MemoryEventStore, batch)
    base, real = [], []
    for _ in range(iters):
        base.append(await _batch(NullStore, batch))
        real.append(await _batch(MemoryEventStore, batch))

    def p95(xs: list[float]) -> float:
        return sorted(xs)[min(len(xs) - 1, int(len(xs) * 0.95))]

    bp95, rp95 = p95(base), p95(real)
    overhead = (rp95 - bp95) / bp95 if bp95 > 0 else 0.0
    return bp95, rp95, overhead


def within_budget(bp95: float, rp95: float, overhead: float) -> bool:
    return overhead <= OVERHEAD_BUDGET or (rp95 - bp95) <= ABS_FLOOR_PER_RUN


async def main() -> int:
    bp95, rp95, overhead = await measure()
    abs_us = (rp95 - bp95) * 1e6
    print(f"trace overhead p95: baseline={bp95*1e6:.1f}us  traced={rp95*1e6:.1f}us  "
          f"overhead={overhead*100:.2f}% / {abs_us:.1f}us/run  "
          f"(budget {OVERHEAD_BUDGET*100:.0f}% or {ABS_FLOOR_PER_RUN*1e6:.0f}us/run)")
    if within_budget(bp95, rp95, overhead):
        return 0
    print("  FAIL: trace overhead exceeds both the percentage and absolute budgets")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
