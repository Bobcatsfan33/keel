from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from ...substrate.events import EventType
from ...executor.engine import RunContext
from ...kir.schema import Node
from .port import ModelPort, ModelRequest, ModelResponse, ModelError


@dataclass(frozen=True)
class Candidate:
    model: str
    max_cost_per_call_usd: float
    capabilities: frozenset[str]


@dataclass(frozen=True)
class ModelPolicy:
    name: str
    candidates: tuple[Candidate, ...]
    escalate_on: frozenset[str]


class Router:
    """Resolves a KIR model_policy to a concrete model at runtime, emitting a
    route.decided event explaining every choice."""

    def __init__(self, policies: dict[str, ModelPolicy], ports: dict[str, ModelPort],
                 default_policy_name: str = "default") -> None:
        self._policies = policies
        self._ports = ports
        self._default_policy_name = default_policy_name

    @staticmethod
    def _provider_of(model: str) -> str:
        return model.split(":", 1)[0]

    async def complete(self, ctx: RunContext, node: Node, req: ModelRequest,
                       required_caps: frozenset[str],
                       under_budget_pressure: bool = False) -> ModelResponse:
        name = node.model_policy if node.model_policy != "default" else self._default_policy_name
        policy = self._policies[name]
        last_error: Optional[Exception] = None
        for i, cand in enumerate(policy.candidates):
            if not required_caps.issubset(cand.capabilities):
                continue
            if under_budget_pressure and "budget_pressure" in policy.escalate_on and i > 0:
                break
            req.model = cand.model
            await ctx.emit(EventType.ROUTE_DECIDED, node_id=node.id, data={
                "policy": policy.name, "chosen": cand.model, "rank": i,
                "reason": "first_capable" if i == 0 else "escalation",
                "budget_pressure": under_budget_pressure})
            try:
                return await self._ports[self._provider_of(cand.model)].complete(req)
            except ModelError as e:
                last_error = e
                if e.taxonomy in ("auth", "permanent", "context_length"):
                    raise
                continue
        raise last_error or ModelError("permanent", "no capable candidate in policy")
