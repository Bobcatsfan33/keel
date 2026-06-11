"""Fast in-suite check of the trace-overhead benchmark (the full CI gate runs
`python -m tests.chaos.bench_overhead` with more iterations)."""
import pytest
from tests.chaos.bench_overhead import measure, within_budget


@pytest.mark.asyncio
async def test_trace_overhead_within_budget():
    bp95, rp95, overhead = await measure(iters=20, warmup=6, batch=15)
    assert within_budget(bp95, rp95, overhead), (
        f"overhead {overhead*100:.2f}% / {(rp95-bp95)*1e6:.0f}us/run over budget")
