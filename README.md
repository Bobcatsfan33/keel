# KEEL — Production-First Agent Runtime

> The keel is the part of the ship nobody sees that keeps it upright.
> Competitors sell sails; we sell the keel.

KEEL is the **runtime layer for AI agents**. Every other agent framework
(CrewAI, LangGraph, AutoGen) competes on *how agents think* — authoring
ergonomics. Their shared, documented failure mode is *how agents run*: no native
observability, no durable state, no cost controls, no testing story, brittle
integrations, no governance. KEEL builds the runtime first and the authoring
ergonomics on top of it.

## Five invariants — enforced, not aspirational

1. **Nothing is invisible.** Every model call, tool call, state mutation, and
   routing decision emits a structured trace event. No code path runs silently.
2. **Nothing is lost.** Every run is resumable from its last completed step after
   process death, deploy, or crash. Durability is not a plugin.
3. **Nothing is unbounded.** Every run executes under an explicit budget (tokens,
   dollars, wall-clock, steps). "Unlimited" must be opted into explicitly.
4. **Everything is testable.** Any recorded run converts to a regression test with
   one command.
5. **The simple path is the safe path.** The 10-minute quickstart produces an app
   that already has tracing, durability, and budgets on. No "production mode" toggle.

## How it works

A run is an **append-only event log**; current state is a pure fold over events.
Resume after a crash and normal scheduling are the *same code path*: fold the log,
compute the runnable frontier, schedule it. Completed model calls are replayed
from the log and **never re-billed**. All nondeterminism (clock, RNG, ids, model
and tool calls) flows through L1 *ports* that record live and replay deterministically.

```
L5  Authoring API (roles/crews DX, decorators)
L4  Graph Compiler (DX -> KIR intermediate representation)
L3  Services (model router, budgeter, tool gateway, eval harness, policy engine)
L2  Durable Executor (event-sourced state machine, checkpoints, retries, HITL)
L1  Substrate (trace bus, storage adapters, OTel export, clock/id/rng ports)
```

## Quickstart

```bash
pip install keel            # SQLite + local trace viewer, zero extra services
keel run examples/research_pipeline.py
keel view                   # browse every run, step, prompt, token, and dollar
```

## Status

Pre-1.0, built in four ~3-month phases toward GA. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the full engineering plan, per-phase
acceptance criteria, and ticket breakdown. Apache-2.0 for Phases 1–3.
