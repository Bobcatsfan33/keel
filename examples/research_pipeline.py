"""Minimal end-to-end KEEL run: a 2-node pipeline on the local durable runtime.

    python examples/research_pipeline.py
"""
import asyncio
from keel.kir.schema import Graph, Node, Edge, NodeType
from keel.substrate.ports import SystemClock, UlidIdGen, SeededRng, MemoryBlobStore
from keel.substrate.store.memory import MemoryEventStore
from keel.substrate.tracebus import TraceBus
from keel.executor.engine import Executor, RunContext
from keel.executor.state import RunState
from keel.services.model.handlers import make_llm_handler, MockModelPort


def build_graph() -> Graph:
    return Graph(
        graph_id="research_pipeline@demo",
        nodes=[
            Node(id="research", type=NodeType.LLM_STEP,
                 config={"prompt": "Research the topic: ", "model": "mock:research"}),
            Node(id="write", type=NodeType.LLM_STEP,
                 config={"prompt": "Write it up: ", "model": "mock:write"}),
        ],
        edges=[Edge.model_validate({"from": "research", "to": "write"})],
    )


async def main() -> None:
    graph = build_graph()
    store = MemoryEventStore()
    bus = TraceBus(store)
    await bus.start()
    blobs = MemoryBlobStore()
    model = MockModelPort(reply='{"summary": "done"}')
    handlers = {NodeType.LLM_STEP: make_llm_handler(model, price_per_1k=(0.003, 0.015))}

    run_id = UlidIdGen().new()
    state = RunState(run_id=run_id, graph=graph)
    ctx = RunContext(run_id, SystemClock(), UlidIdGen(), SeededRng(0), blobs, bus, state)
    final = await Executor(store, bus, blobs, handlers).run(graph, ctx)
    await bus.flush()
    await bus.close()

    print(f"run {run_id} -> {final.status}")
    print("steps:", {k: v.status for k, v in final.steps.items()})
    print(f"total cost: ${final.total_cost_usd:.6f}  tokens in/out: "
          f"{final.total_tokens_in}/{final.total_tokens_out}  model calls: {model.calls}")


if __name__ == "__main__":
    asyncio.run(main())
