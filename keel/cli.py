"""``keel`` command-line entrypoint.

    keel run <file.py>        run a graph/crew on the durable local runtime
    keel ls                   list recorded runs
    keel show <run_id>        print a run's full event timeline (the trace)
    keel resume <run_id>      continue a crashed/paused run (no re-billing)
    keel approve <run_id> <node> [--reject]   decide a human gate, then resume
    keel replay <run_id>      re-drive a run from its recorded log (byte-identity check)
    keel diff <run_a> <run_b> show where two runs diverge (prompt/route/cost)
    keel simulate <file.py>   estimate a graph's cost before running it
    keel test record <run_id> scaffold an eval case from a recorded run
    keel audit export <run_id> emit a self-contained run bundle
    keel view                 launch the trace viewer
"""
from __future__ import annotations
import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional, NoReturn

from .kir.schema import Graph
from .services.runner import Runner
from .services.model.handlers import MockModelPort
from .services.model.pricing import PriceTable, estimate_cost


# --------------------------------------------------------------------------- #
# graph loading
# --------------------------------------------------------------------------- #
def _load_graph(path: str) -> Graph:
    p = Path(path)
    if not p.exists():
        _die(f"file not found: {path}")
    spec = importlib.util.spec_from_file_location("keel_user_graph", p)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["keel_user_graph"] = module
    spec.loader.exec_module(module)

    # A module-level Graph, or a Crew instance (has .compile()).
    for name in ("graph", "GRAPH", "crew", "CREW"):
        obj = getattr(module, name, None)
        if isinstance(obj, Graph):
            return obj
        if obj is not None and not callable(obj) and hasattr(obj, "compile"):
            return _as_graph(obj.compile())
    # Or a factory function returning a Graph or Crew.
    for name in ("build_graph", "build_crew", "make_graph", "make_crew"):
        fn = getattr(module, name, None)
        if callable(fn):
            result = fn()
            return _as_graph(result.compile() if hasattr(result, "compile") else result)
    _die(f"{path}: define a module-level `graph`, a `build_graph()` function, or a `crew`")


def _as_graph(obj: Any) -> Graph:
    if not isinstance(obj, Graph):
        _die("loaded object is not a KIR Graph (did you forget Crew.compile()?)")
    return obj


def _build_model(args: argparse.Namespace) -> Any:
    if getattr(args, "model", None) and not getattr(args, "mock", False):
        from .services.model.providers import build_provider, provider_of
        return build_provider(provider_of(args.model))
    if getattr(args, "model", None) is None or getattr(args, "mock", False):
        if not getattr(args, "quiet", False):
            print("[keel] no --model set; using the deterministic mock model "
                  "(set --model anthropic:... for live calls)", file=sys.stderr)
    return MockModelPort(reply=getattr(args, "mock_reply", None) or '{"ok": true}')


async def _runner(args: argparse.Namespace, model: Optional[Any] = None) -> Runner:
    return await Runner.open(db_path=args.db, blob_dir=args.blobs, model=model)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
async def cmd_run(args: argparse.Namespace) -> int:
    graph = _load_graph(args.file)
    runner = await _runner(args, _build_model(args))
    try:
        state = await runner.run(graph, run_id=args.run_id)
    finally:
        await runner.close()
    _print_summary(state.run_id, state)
    return 0 if state.status == "completed" else 1


async def cmd_ls(args: argparse.Namespace) -> int:
    runner = await _runner(args)
    try:
        runs = await runner.list_runs(args.limit)
    finally:
        await runner.close()
    if not runs:
        print("no runs recorded")
        return 0
    for r in runs:
        print(f"{r.run_id}  {r.graph_id:<30}  {r.created_at}")
    return 0


async def cmd_show(args: argparse.Namespace) -> int:
    runner = await _runner(args)
    try:
        events = await runner.read_events(args.run_id)
    finally:
        await runner.close()
    if not events:
        _die(f"no events for run '{args.run_id}'")
    for e in events:
        node = f" {e.node_id}" if e.node_id else ""
        cost = f" ${e.cost_usd:.5f}" if e.cost_usd else ""
        tok = f" [{e.tokens.input}->{e.tokens.output}]" if e.tokens else ""
        marker = "  <-- RESUME SEAM" if e.type.value == "run.resumed" else ""
        print(f"#{e.seq:>4} {e.type.value:<22}{node}{tok}{cost}{marker}")
    return 0


async def cmd_resume(args: argparse.Namespace) -> int:
    runner = await _runner(args, _build_model(args))
    try:
        state = await runner.resume(args.run_id)
    finally:
        await runner.close()
    _print_summary(args.run_id, state)
    return 0 if state.status == "completed" else 1


async def cmd_approve(args: argparse.Namespace) -> int:
    runner = await _runner(args, _build_model(args))
    try:
        if args.reject:
            await runner.reject_gate(args.run_id, args.node)
            print(f"rejected gate {args.node} on {args.run_id}")
        else:
            payload = args.payload.encode() if args.payload else None
            await runner.approve_gate(args.run_id, args.node, payload)
            print(f"approved gate {args.node} on {args.run_id}")
        if args.resume:
            state = await runner.resume(args.run_id)
            _print_summary(args.run_id, state)
    finally:
        await runner.close()
    return 0


async def cmd_replay(args: argparse.Namespace) -> int:
    from .executor.replay import verify_recorded_replay
    runner = await _runner(args)
    try:
        graph_json = await runner.catalog.get_graph(args.run_id)
        if graph_json is None:
            _die(f"unknown run '{args.run_id}'")
        graph = Graph.model_validate_json(graph_json)
        events = await runner.read_events(args.run_id)
    finally:
        await runner.close()
    ok, detail = verify_recorded_replay(graph, args.run_id, events)
    print(f"replay {'OK (byte-identical)' if ok else 'DIVERGED'}: {detail}")
    return 0 if ok else 1


async def cmd_diff(args: argparse.Namespace) -> int:
    runner = await _runner(args)
    try:
        a = await runner.read_events(args.run_a)
        b = await runner.read_events(args.run_b)
    finally:
        await runner.close()
    from .executor.replay import diff_runs
    for line in diff_runs(a, b):
        print(line)
    return 0


async def cmd_simulate(args: argparse.Namespace) -> int:
    graph = _load_graph(args.file)
    table = PriceTable()
    total = 0.0
    tin = int(args.assume_input_tokens)
    tout = int(args.assume_output_tokens)
    print(f"cost simulation for {graph.graph_id} "
          f"(assuming {tin} in / {tout} out per llm step):")
    for n in graph.nodes:
        if n.type.value in ("llm_step", "router"):
            model = str(n.config.get("model", "anthropic:claude-haiku-4-5"))
            c = estimate_cost(model, tin, tout, table) if n.type.value == "llm_step" else 0.0
            total += c
            print(f"  {n.id:<28} {model:<28} ${c:.5f}")
    print(f"  estimated total: ${total:.5f}")
    if table.unknown:
        print(f"  (unpriced models, counted as $0: {', '.join(sorted(table.unknown))})")
    return 0


async def cmd_test(args: argparse.Namespace) -> int:
    if args.action != "record":
        _die("usage: keel test record <run_id>")
    runner = await _runner(args)
    try:
        state = await runner.load_state(args.run_id)
    finally:
        await runner.close()
    assertions = []
    for n in state.graph.nodes:
        if n.output_schema:
            assertions.append({"type": "schema", "node_id": n.id, "expected": n.output_schema})
    case = {"case_id": f"{state.graph.graph_id}:{args.run_id}",
            "graph_id": state.graph.graph_id, "recorded_run_id": args.run_id,
            "assertions": assertions}
    out = Path(args.out or f"evalcase_{args.run_id}.json")
    out.write_text(json.dumps(case, indent=2))
    print(f"wrote eval case -> {out} ({len(assertions)} assertion(s))")
    return 0


async def cmd_audit(args: argparse.Namespace) -> int:
    if args.action != "export":
        _die("usage: keel audit export <run_id>")
    runner = await _runner(args)
    try:
        events = await runner.read_events(args.run_id)
        graph_json = await runner.catalog.get_graph(args.run_id)
    finally:
        await runner.close()
    bundle = {
        "run_id": args.run_id,
        "graph": json.loads(graph_json) if graph_json else None,
        "events": [json.loads(e.to_json()) for e in events],
        "note": "tamper-evident hash chain + signature land in P4-6 (audit log).",
    }
    out = Path(args.out or f"audit_{args.run_id}.json")
    out.write_text(json.dumps(bundle, indent=2))
    print(f"wrote audit bundle -> {out} ({len(events)} events)")
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    try:
        from .viewer.app import serve
    except ImportError:
        _die("viewer extra not installed. Install with: pip install 'keel[viewer]'")
    print(f"[keel] viewer on http://{args.host}:{args.port}  (db={args.db})")
    serve(db_path=args.db, blob_dir=args.blobs, host=args.host, port=args.port)
    return 0


# --------------------------------------------------------------------------- #
# helpers + parser
# --------------------------------------------------------------------------- #
def _print_summary(run_id: str, state: Any) -> None:
    print(f"run {run_id} -> {state.status}")
    print("  steps:", {k: v.status for k, v in state.steps.items()})
    print(f"  cost ${state.total_cost_usd:.6f}  tokens "
          f"{state.total_tokens_in}->{state.total_tokens_out}")


def _die(msg: str) -> NoReturn:
    print(f"keel: error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _add_store_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default=os.environ.get("KEEL_DB", "keel.db"))
    p.add_argument("--blobs", default=os.environ.get("KEEL_BLOBS", "blobs"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keel", description="KEEL agent runtime")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run a graph/crew file")
    p_run.add_argument("file")
    p_run.add_argument("--run-id", dest="run_id", default=None)
    p_run.add_argument("--model", default=os.environ.get("KEEL_MODEL"))
    p_run.add_argument("--mock", action="store_true")
    p_run.add_argument("--mock-reply", dest="mock_reply", default=None)
    p_run.add_argument("--quiet", action="store_true")
    _add_store_args(p_run)
    p_run.set_defaults(func=cmd_run, _async=True)

    p_ls = sub.add_parser("ls", help="list recorded runs")
    p_ls.add_argument("--limit", type=int, default=50)
    _add_store_args(p_ls)
    p_ls.set_defaults(func=cmd_ls, _async=True)

    p_show = sub.add_parser("show", help="print a run's event timeline")
    p_show.add_argument("run_id")
    _add_store_args(p_show)
    p_show.set_defaults(func=cmd_show, _async=True)

    p_res = sub.add_parser("resume", help="continue a crashed/paused run")
    p_res.add_argument("run_id")
    p_res.add_argument("--model", default=os.environ.get("KEEL_MODEL"))
    p_res.add_argument("--mock", action="store_true")
    p_res.add_argument("--quiet", action="store_true")
    _add_store_args(p_res)
    p_res.set_defaults(func=cmd_resume, _async=True)

    p_app = sub.add_parser("approve", help="approve/reject a human gate")
    p_app.add_argument("run_id")
    p_app.add_argument("node")
    p_app.add_argument("--reject", action="store_true")
    p_app.add_argument("--payload", default=None)
    p_app.add_argument("--resume", action="store_true", help="resume after deciding")
    p_app.add_argument("--model", default=os.environ.get("KEEL_MODEL"))
    p_app.add_argument("--mock", action="store_true")
    p_app.add_argument("--quiet", action="store_true")
    _add_store_args(p_app)
    p_app.set_defaults(func=cmd_approve, _async=True)

    p_rep = sub.add_parser("replay", help="byte-identical recorded replay check")
    p_rep.add_argument("run_id")
    _add_store_args(p_rep)
    p_rep.set_defaults(func=cmd_replay, _async=True)

    p_diff = sub.add_parser("diff", help="diff two runs")
    p_diff.add_argument("run_a")
    p_diff.add_argument("run_b")
    _add_store_args(p_diff)
    p_diff.set_defaults(func=cmd_diff, _async=True)

    p_sim = sub.add_parser("simulate", help="estimate a graph's cost")
    p_sim.add_argument("file")
    p_sim.add_argument("--assume-input-tokens", default=1000)
    p_sim.add_argument("--assume-output-tokens", default=500)
    p_sim.set_defaults(func=cmd_simulate, _async=True)

    p_test = sub.add_parser("test", help="eval-case tooling")
    p_test.add_argument("action", choices=["record"])
    p_test.add_argument("run_id")
    p_test.add_argument("--out", default=None)
    _add_store_args(p_test)
    p_test.set_defaults(func=cmd_test, _async=True)

    p_aud = sub.add_parser("audit", help="export a run bundle")
    p_aud.add_argument("action", choices=["export"])
    p_aud.add_argument("run_id")
    p_aud.add_argument("--out", default=None)
    _add_store_args(p_aud)
    p_aud.set_defaults(func=cmd_audit, _async=True)

    p_view = sub.add_parser("view", help="launch the trace viewer")
    p_view.add_argument("--host", default="127.0.0.1")
    p_view.add_argument("--port", type=int, default=8765)
    _add_store_args(p_view)
    p_view.set_defaults(func=cmd_view, _async=False)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "_async", False):
        return int(asyncio.run(args.func(args)))
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
