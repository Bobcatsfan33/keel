from __future__ import annotations
import json
from ...substrate.events import EventType, TokenUsage
from ...executor.engine import RunContext, FatalError
from ...kir.schema import Node
from .port import ModelPort, ModelRequest, ModelResponse


def make_llm_handler(model_port: ModelPort, price_per_1k: tuple[float, float] = (0.0, 0.0)):
    """Returns a node handler that runs an llm_step: emits request/response events
    with tokens + cost, and enforces structured output via bounded re-prompt."""

    def _price(resp: ModelResponse) -> float:
        return (resp.tokens_in / 1000) * price_per_1k[0] + (resp.tokens_out / 1000) * price_per_1k[1]

    async def handle(ctx: RunContext, node: Node, inputs: dict) -> bytes:
        upstream = {k: v.decode() for k, v in inputs.items()}
        messages = [{"role": "user",
                     "content": node.config.get("prompt", "") + json.dumps(upstream)}]
        req = ModelRequest(model=node.config.get("model", "mock:test"),
                           messages=messages, max_tokens=node.budget.max_tokens or 1024)

        max_reprompts = 2
        last_err: str | None = None
        for attempt in range(max_reprompts + 1):
            await ctx.emit(EventType.LLM_REQUEST, node_id=node.id,
                           payload=json.dumps(req.messages).encode(),
                           data={"model": req.model, "reprompt": attempt})
            resp = await model_port.complete(req)
            await ctx.emit(EventType.LLM_RESPONSE, node_id=node.id,
                           payload=resp.text.encode(),
                           tokens=TokenUsage(input=resp.tokens_in, output=resp.tokens_out,
                                             model=resp.model),
                           cost_usd=_price(resp))
            if not node.output_schema:
                return resp.text.encode()
            try:
                json.loads(resp.text)  # placeholder for Pydantic schema validation
                return resp.text.encode()
            except json.JSONDecodeError as e:
                last_err = str(e)
                req.messages = messages + [
                    {"role": "assistant", "content": resp.text},
                    {"role": "user", "content": f"Invalid JSON ({e}). Return ONLY valid JSON."},
                ]
        raise FatalError(f"structured_output_unsatisfied after {max_reprompts} reprompts: {last_err}")

    return handle


class MockModelPort:
    """Deterministic test/dev model. Counts calls so tests can assert that resumed
    runs do NOT re-invoke completed steps."""

    def __init__(self, reply: str = '{"ok": true}') -> None:
        self.reply = reply
        self.calls = 0

    async def complete(self, req: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(text=self.reply, tokens_in=10, tokens_out=5, model=req.model)

    async def stream(self, req: ModelRequest):
        yield self.reply

    def count_tokens(self, text: str, model: str) -> int:
        return max(1, len(text) // 4)
