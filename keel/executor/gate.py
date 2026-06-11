from __future__ import annotations
from ..substrate.events import EventType
from .engine import RunContext, FatalError
from ..kir.schema import Node


class GatePaused(Exception):
    """Unwinds the run loop cleanly. The run is NOT failed — it is parked in the
    log with zero compute until an external approval appends a GATE_* event."""


def _decision_for(ctx: RunContext, node_id: str) -> str | None:
    # In a full build this reads the run's events; the in-memory state carries the
    # last decision via config injected by GateService. Kept simple here.
    return ctx.state.steps.get(node_id).error.get("decision") if (
        ctx.state.steps.get(node_id) and ctx.state.steps[node_id].error) else None


async def human_gate_handler(ctx: RunContext, node: Node, inputs: dict) -> bytes:
    decision = node.config.get("_decision")  # injected on resume after approval
    if decision is None:
        await ctx.emit(EventType.GATE_OPENED, node_id=node.id,
                       data={"prompt": node.config.get("prompt", ""),
                             "ttl_s": node.config.get("ttl_s")})
        await ctx.emit(EventType.RUN_PAUSED)
        ctx.state.status = "paused"
        raise GatePaused(node.id)
    if decision == "rejected":
        raise FatalError(f"human rejected gate {node.id}")
    return node.config.get("_payload", b"approved")
