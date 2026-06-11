"""P4-6: the audit bundle is independently verifiable and any tampering (edit,
reorder, insert, drop) breaks the hash chain. Optional HMAC signature over the head."""
import pytest

from keel.kir.schema import Graph, Node, Edge, NodeType
from keel.services.runner import Runner
from keel.services.model.handlers import MockModelPort
from keel.services.audit import make_bundle, verify_bundle


def _graph() -> Graph:
    return Graph(graph_id="g",
                 nodes=[Node(id="a", type=NodeType.LLM_STEP, config={"model": "mock:test"}),
                        Node(id="b", type=NodeType.LLM_STEP, config={"model": "mock:test"})],
                 edges=[Edge.model_validate({"from": "a", "to": "b"})])


async def _bundle(secret=None):
    runner = await Runner.open(in_memory=True, model=MockModelPort())
    await runner.run(_graph(), run_id="r")
    events = await runner.read_events("r")
    bundle = make_bundle("r", events, secret=secret)
    await runner.close()
    return bundle


@pytest.mark.asyncio
async def test_clean_bundle_verifies():
    bundle = await _bundle()
    ok, detail = verify_bundle(bundle)
    assert ok, detail


@pytest.mark.asyncio
async def test_edited_event_breaks_chain():
    bundle = await _bundle()
    # tamper: flip a cost in the middle of the log
    for e in bundle["events"]:
        if e["type"] == "llm.response":
            e["cost_usd"] = 999.0
            break
    ok, detail = verify_bundle(bundle)
    assert not ok and "chain broken" in detail


@pytest.mark.asyncio
async def test_dropped_event_detected():
    bundle = await _bundle()
    del bundle["events"][3]  # drop one event but leave the chain claims
    ok, _ = verify_bundle(bundle)
    assert not ok


@pytest.mark.asyncio
async def test_reordered_events_detected():
    bundle = await _bundle()
    bundle["events"][2], bundle["events"][3] = bundle["events"][3], bundle["events"][2]
    ok, _ = verify_bundle(bundle)
    assert not ok


@pytest.mark.asyncio
async def test_signed_bundle_and_signature_tamper():
    bundle = await _bundle(secret="audit-key")
    ok, _ = verify_bundle(bundle, secret="audit-key")
    assert ok
    # wrong key fails
    assert not verify_bundle(bundle, secret="wrong-key")[0]
    # forged head without matching signature fails
    bundle["head"] = "f" * 64
    assert not verify_bundle(bundle, secret="audit-key")[0]
