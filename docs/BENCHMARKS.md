# KEEL benchmarks

Reproduce: `python -m bench.run_benchmarks` (from the repo root, with `.[dev,viewer]`).
All KEEL numbers below are measured on this machine; competitor columns are TODO
(they need the frameworks installed and an identical workload — not fabricated here).

| Metric | KEEL | CrewAI | LangGraph |
|--------|------|--------|-----------|
| Trace overhead (p50, best-of-3, median) | 0.31% / 4µs per run | n/a (no native tracing) | TODO |
| Viewer render (9002 events) | 93 ms | n/a | n/a |
| Context-compiler token reduction | 86% | TODO | TODO |
| Crash-resume (1002-event run) | 2 ms | not durable | TODO |
| Durability (kill-9 mid-run -> resume) | yes, no re-billing | no | partial |
| Budget enforcement (no-bypass) | yes (emit chokepoint) | no | no |

Notes:
- Trace overhead is traced-runtime vs a /dev/null sink; tracing cannot be disabled.
- Token reduction is the staged context compiler vs naive concatenation.
- Durability/budget rows are capability comparisons, not timings.
