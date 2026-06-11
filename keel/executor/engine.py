from __future__ import annotations
import asyncio
import math
from typing import Optional, Callable, Awaitable
from ..substrate.events import Event, EventType, TokenUsage
from ..substrate.ports import Clock, IdGen, Rng, BlobStore
from ..substrate.tracebus import TraceBus
from ..substrate.store.base import EventStore
from ..kir.schema import Graph, Node, NodeType
from .state import RunState


class RetryableError(Exception):
    def __init__(self, msg: str, taxonomy: str = "transient") -> None:
        super().__init__(msg)
        self.taxonomy = taxonomy


class FatalError(Exception):
    pass


class RunContext:
    """One per run. Holds the injected ports so nothing below calls a clock, rng,
    or id generator directly. During replay these are the Replay* variants."""

    def __init__(self, run_id: str, clock: Clock, ids: IdGen, rng: Rng,
                 blobs: BlobStore, bus: TraceBus, state: RunState) -> None:
        self.run_id = run_id
        self.clock = clock
        self.ids = ids
        self.rng = rng
        self.blobs = blobs
        self.bus = bus
        self.state = state

    async def emit(self, type: EventType, *, node_id: Optional[str] = None,
                   attempt: int = 1, payload: Optional[bytes] = None,
                   tokens: Optional[TokenUsage] = None, cost_usd: float = 0.0,
                   data: Optional[dict] = None) -> Event:
        ref = self.blobs.put(payload) if payload is not None else None
        ev = Event(
            event_id=self.ids.new(),
            run_id=self.run_id,
            seq=self.state.next_seq,
            ts=self.clock.now(),
            type=type,
            node_id=node_id,
            attempt=attempt,
            payload_ref=ref,
            tokens=tokens,
            cost_usd=cost_usd,
            data=data or {},
        )
        # Fold locally FIRST (advances seq), then persist. A crash in the gap leaves
        # the persisted log gap-free; the un-persisted event is regenerated on resume.
        self.state._apply(ev)
        await self.bus.emit(ev)
        return ev


NodeHandler = Callable[[RunContext, Node, dict], Awaitable[bytes]]


class Executor:
    def __init__(self, store: EventStore, bus: TraceBus, blobs: BlobStore,
                 handlers: dict[NodeType, NodeHandler]) -> None:
        self._store = store
        self._bus = bus
        self._blobs = blobs
        self._handlers = handlers

    async def run(self, graph: Graph, ctx: RunContext) -> RunState:
        st = ctx.state
        if st.status == "pending":
            await ctx.emit(EventType.RUN_STARTED)
        elif st.status in ("running", "paused"):
            await ctx.emit(EventType.RUN_RESUMED)

        while True:
            frontier = st.frontier()
            if not frontier:
                break
            await asyncio.gather(*(self._run_node(graph, ctx, nid) for nid in frontier))
            if st.status in ("paused", "failed"):
                return st

        await ctx.emit(EventType.RUN_COMPLETED)
        return st

    async def _run_node(self, graph: Graph, ctx: RunContext, node_id: str) -> None:
        node = next(n for n in graph.nodes if n.id == node_id)
        rec = ctx.state.steps.get(node_id)
        attempt = (rec.attempt + 1) if rec and rec.status == "started" else 1

        await ctx.emit(EventType.STEP_SCHEDULED, node_id=node_id, attempt=attempt)
        await ctx.emit(EventType.STEP_STARTED, node_id=node_id, attempt=attempt)

        handler = self._handlers[node.type]
        inputs = self._gather_inputs(graph, ctx, node)
        try:
            result_bytes = await handler(ctx, node, inputs)
        except RetryableError as err:
            if attempt <= node.retry.max:
                await ctx.emit(EventType.STEP_RETRIED, node_id=node_id, attempt=attempt,
                               data={"reason": err.taxonomy})
                await self._backoff(node, attempt)
                return await self._run_node(graph, ctx, node_id)
            await ctx.emit(EventType.STEP_FAILED, node_id=node_id, attempt=attempt,
                           data={"error": {"type": err.taxonomy, "msg": str(err)}})
            await ctx.emit(EventType.RUN_FAILED)
            ctx.state.status = "failed"
            return
        except FatalError as err:
            await ctx.emit(EventType.STEP_FAILED, node_id=node_id, attempt=attempt,
                           data={"error": {"type": "permanent", "msg": str(err)}})
            await ctx.emit(EventType.RUN_FAILED)
            ctx.state.status = "failed"
            return

        await ctx.emit(EventType.STEP_COMPLETED, node_id=node_id, attempt=attempt,
                       payload=result_bytes)

    def _gather_inputs(self, graph: Graph, ctx: RunContext, node: Node) -> dict:
        """Pull predecessors' completed outputs from the blob store. A resumed run
        reads them back instead of recomputing — the 'never re-billed' guarantee."""
        preds = [e.from_ for e in graph.edges if e.to == node.id]
        out: dict = {}
        for p in preds:
            rec = ctx.state.steps.get(p)
            if rec and rec.result_ref:
                out[p] = ctx.blobs.get(rec.result_ref)
        return out

    async def _backoff(self, node: Node, attempt: int) -> None:
        if node.retry.backoff == "none":
            return
        base = 0.5
        delay = base * attempt if node.retry.backoff == "linear" else base * math.pow(2, attempt)
        await asyncio.sleep(delay)
