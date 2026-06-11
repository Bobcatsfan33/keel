from __future__ import annotations
import json
from typing import Callable, Awaitable, Optional, AsyncIterator
from pydantic import ValidationError
from ...substrate.events import EventType, TokenUsage
from ...executor.engine import RunContext, FatalError, NodeHandler
from ...kir.schema import Node
from ...kir.schemas_registry import resolve_schema
from .port import ModelPort, ModelRequest, ModelResponse
from .pricing import PriceTable

# A Completer turns (run context, node, request) into a response. A plain ModelPort
# becomes one via port_completer(); the Router is one directly. This is what lets the
# same handler back both the "one model" and the "model_policy" execution styles.
Completer = Callable[[RunContext, Node, ModelRequest], Awaitable[ModelResponse]]

MAX_REPROMPTS = 2


def port_completer(port: ModelPort) -> Completer:
    async def _complete(ctx: RunContext, node: Node, req: ModelRequest) -> ModelResponse:
        return await port.complete(req)
    return _complete


def _assemble_prompt(node: Node, inputs: dict[str, bytes]) -> list[dict[str, str]]:
    """Phase-1 prompt assembly. Phase 3 (P3-4) replaces this with the measured,
    staged context compiler. Kept deterministic so golden tests are stable."""
    upstream = {k: v.decode("utf-8", errors="replace") for k, v in sorted(inputs.items())}
    system = node.config.get("system")
    messages: list[dict[str, str]] = []
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    user = str(node.config.get("prompt", ""))
    if upstream:
        user = f"{user}\n\nInputs:\n{json.dumps(upstream, sort_keys=True)}"
    messages.append({"role": "user", "content": user})
    return messages


def make_llm_handler(
    model: "ModelPort | Completer",
    price_per_1k: Optional[tuple[float, float]] = None,
    *,
    price_table: Optional[PriceTable] = None,
) -> NodeHandler:
    """Build a handler for ``llm_step`` nodes.

    Emits an ``llm.request``/``llm.response`` pair per attempt with tokens and cost
    attached (invariant #1), and — when the node declares ``output_schema`` —
    enforces it with a bounded re-prompt loop that feeds the validator's own error
    back to the model. The step ends in typed success or a typed failure event;
    malformed output never reaches a downstream node (P1-7).
    """
    completer: Completer = port_completer(model) if isinstance(model, ModelPort) else model
    table = price_table or (
        _FlatTable(price_per_1k) if price_per_1k is not None else PriceTable()
    )

    async def handle(ctx: RunContext, node: Node, inputs: dict[str, bytes]) -> bytes:
        base_messages = _assemble_prompt(node, inputs)
        schema_model = resolve_schema(node.output_schema)
        req = ModelRequest(
            model=str(node.config.get("model", "mock:test")),
            messages=[dict(m) for m in base_messages],
            max_tokens=node.budget.max_tokens or 4096,
            temperature=float(node.config.get("temperature", 0.0)),
            response_schema=schema_model.model_json_schema() if schema_model else None,
        )

        last_err: str | None = None
        for attempt in range(MAX_REPROMPTS + 1):
            await ctx.emit(
                EventType.LLM_REQUEST, node_id=node.id,
                payload=json.dumps(req.messages).encode(),
                data={"model": req.model, "reprompt": attempt},
            )
            resp = await completer(ctx, node, req)
            await ctx.emit(
                EventType.LLM_RESPONSE, node_id=node.id,
                payload=resp.text.encode(),
                tokens=TokenUsage(input=resp.tokens_in, output=resp.tokens_out, model=resp.model),
                cost_usd=table.cost(resp),
            )

            if schema_model is None:
                return resp.text.encode()
            try:
                validated = schema_model.model_validate_json(resp.text)
                return validated.model_dump_json().encode()
            except ValidationError as ve:
                last_err = _summarize_validation_error(ve)
                req.messages = [dict(m) for m in base_messages] + [
                    {"role": "assistant", "content": resp.text},
                    {"role": "user",
                     "content": f"Your output failed schema validation:\n{last_err}\n"
                                "Return ONLY valid JSON matching the schema."},
                ]

        raise FatalError(
            f"structured_output_unsatisfied after {MAX_REPROMPTS} reprompts: {last_err}"
        )

    return handle


def _summarize_validation_error(ve: ValidationError) -> str:
    parts = []
    for err in ve.errors()[:6]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg', '')}")
    return "; ".join(parts)


class _FlatTable(PriceTable):
    """A PriceTable that applies one (in, out) rate to every model — used by the
    legacy ``price_per_1k`` argument."""

    def __init__(self, rate: tuple[float, float]) -> None:
        super().__init__(prices={})
        self._flat = rate

    def rate(self, model: str) -> tuple[float, float]:
        return self._flat


# --------------------------------------------------------------------------- #
# Test / dev model doubles
# --------------------------------------------------------------------------- #
class MockModelPort:
    """Deterministic dev/test model. Counts calls so tests can assert that resumed
    runs do NOT re-invoke completed steps."""

    def __init__(self, reply: str = '{"ok": true}') -> None:
        self.reply = reply
        self.calls = 0

    async def complete(self, req: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(text=self.reply, tokens_in=10, tokens_out=5, model=req.model)

    async def stream(self, req: ModelRequest) -> AsyncIterator[str]:
        yield self.reply

    def count_tokens(self, text: str, model: str) -> int:
        return max(1, len(text) // 4)


class ScriptedModelPort:
    """Returns a scripted sequence of replies, then repeats the last. Lets a chaos
    test drive the structured-output re-prompt loop: malformed, malformed, valid."""

    def __init__(self, replies: list[str]) -> None:
        if not replies:
            raise ValueError("ScriptedModelPort needs at least one reply")
        self._replies = replies
        self.calls = 0

    async def complete(self, req: ModelRequest) -> ModelResponse:
        text = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return ModelResponse(text=text, tokens_in=10, tokens_out=5, model=req.model)

    async def stream(self, req: ModelRequest) -> AsyncIterator[str]:
        yield self._replies[min(self.calls, len(self._replies) - 1)]

    def count_tokens(self, text: str, model: str) -> int:
        return max(1, len(text) // 4)


class AdversarialModelPort:
    """Always returns output that fails the schema. The handler must converge to a
    typed FatalError, never leak malformed output downstream."""

    def __init__(self, bad: str = "not json at all {{{") -> None:
        self._bad = bad
        self.calls = 0

    async def complete(self, req: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(text=self._bad, tokens_in=10, tokens_out=5, model=req.model)

    async def stream(self, req: ModelRequest) -> AsyncIterator[str]:
        yield self._bad

    def count_tokens(self, text: str, model: str) -> int:
        return max(1, len(text) // 4)


__all__ = [
    "make_llm_handler", "port_completer", "Completer", "MAX_REPROMPTS",
    "MockModelPort", "ScriptedModelPort", "AdversarialModelPort",
]
