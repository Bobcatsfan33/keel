from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from ..substrate.ports import Clock


class BudgetAction(str, Enum):
    WARN = "warn"
    PAUSE = "pause"
    KILL = "kill"


@dataclass(frozen=True)
class Budget:
    max_usd: Optional[float] = None
    max_tokens: Optional[int] = None
    max_steps: Optional[int] = None
    max_wallclock_s: Optional[float] = None
    action: BudgetAction = BudgetAction.PAUSE
    warn_at: float = 0.8

    @staticmethod
    def default() -> "Budget":
        # Ships with EVERY run unless explicitly overridden. 'unlimited' must be a
        # literal Budget(max_usd=None, ...) written on purpose.
        return Budget(max_usd=5.0, max_steps=100, action=BudgetAction.PAUSE)


@dataclass
class Meter:
    usd: float = 0.0
    tokens: int = 0
    steps: int = 0
    started_monotonic: float = 0.0


class BudgetExceeded(Exception):
    def __init__(self, scope: str, dimension: str, limit: float, action: BudgetAction) -> None:
        super().__init__(f"budget {scope}.{dimension} exceeded (limit={limit})")
        self.scope = scope
        self.dimension = dimension
        self.action = action


class Budgeter:
    """Nested scopes (tenant -> run -> crew -> node). would_breach() is a pure
    predicate so the executor can decide-then-act atomically; commit() applies a
    spend across all ancestor scopes. There is no spend path that bypasses this."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._budgets: dict[str, Budget] = {}
        self._meters: dict[str, Meter] = {}

    def register(self, scope: str, budget: Budget) -> None:
        self._budgets[scope] = budget
        self._meters[scope] = Meter(started_monotonic=self._clock.monotonic())

    def _scopes_for(self, node_scope: str) -> list[str]:
        parts = node_scope.split("/")
        return ["/".join(parts[: i + 1]) for i in range(len(parts))]

    def would_breach(self, node_scope: str, add_usd: float, add_tokens: int) -> Optional[BudgetExceeded]:
        for scope in self._scopes_for(node_scope):
            b = self._budgets.get(scope)
            m = self._meters.get(scope)
            if not b or not m:
                continue
            if b.max_usd is not None and m.usd + add_usd > b.max_usd:
                return BudgetExceeded(scope, "usd", b.max_usd, b.action)
            if b.max_tokens is not None and m.tokens + add_tokens > b.max_tokens:
                return BudgetExceeded(scope, "tokens", b.max_tokens, b.action)
            if b.max_steps is not None and m.steps + 1 > b.max_steps:
                return BudgetExceeded(scope, "steps", b.max_steps, b.action)
            if b.max_wallclock_s is not None:
                if self._clock.monotonic() - m.started_monotonic > b.max_wallclock_s:
                    return BudgetExceeded(scope, "wallclock", b.max_wallclock_s, b.action)
        return None

    def commit(self, node_scope: str, add_usd: float, add_tokens: int) -> list[str]:
        warnings: list[str] = []
        for scope in self._scopes_for(node_scope):
            b, m = self._budgets.get(scope), self._meters.get(scope)
            if not b or not m:
                continue
            m.usd += add_usd
            m.tokens += add_tokens
            m.steps += 1
            if b.max_usd and m.usd >= b.warn_at * b.max_usd:
                warnings.append(scope)
        return warnings
