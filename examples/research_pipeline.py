"""End-to-end KEEL quickstart: author a crew, run it on the durable runtime.

    python examples/research_pipeline.py        # uses the deterministic mock model
    keel run examples/research_pipeline.py      # same, via the CLI
    keel view                                   # browse the trace

The same file works under ``keel run`` because it exposes a module-level ``crew``.
"""
import asyncio

from keel.authoring import Agent, Task, Crew
from keel.services.runner import Runner
from keel.services.model.handlers import MockModelPort

researcher = Agent("researcher", goal="Find the key facts on the topic")
writer = Agent("writer", goal="Write a clear, sourced summary from the research")

research = Task("Research the topic thoroughly", agent=researcher)
write = Task("Write the article from the research", agent=writer, context=[research])

crew = Crew("research_pipeline", tasks=[research, write])


async def main() -> None:
    graph = crew.compile()
    runner = await Runner.open(in_memory=True, model=MockModelPort(reply='{"summary": "done"}'))
    state = await runner.run(graph, run_id="example-1")
    await runner.close()
    print(f"run {state.run_id} -> {state.status}")
    print("steps:", {k: v.status for k, v in state.steps.items()})
    print(f"cost ${state.total_cost_usd:.6f}  tokens "
          f"{state.total_tokens_in}->{state.total_tokens_out}")


if __name__ == "__main__":
    asyncio.run(main())
