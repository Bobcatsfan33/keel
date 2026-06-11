"""P4-4: HMAC-verified webhook trigger starts a run; bad/missing signature is
rejected; unknown graph is 404."""
import hashlib
import hmac
import pytest

from keel.kir.schema import Graph, Node, NodeType
from keel.services.runner import Runner
from keel.services.model.handlers import MockModelPort
from keel.services.triggers import verify_hmac, TriggerService

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from keel.services.triggers import create_trigger_app  # noqa: E402

SECRET = "topsecret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_verify_hmac():
    body = b'{"x":1}'
    assert verify_hmac(SECRET, body, _sign(body))
    assert not verify_hmac(SECRET, body, "sha256=deadbeef")
    assert not verify_hmac(SECRET, body, None)


@pytest.mark.asyncio
async def test_webhook_trigger_starts_run():
    runner = await Runner.open(in_memory=True, model=MockModelPort())
    graph = Graph(graph_id="ingest", nodes=[Node(id="n", type=NodeType.LLM_STEP,
                  config={"model": "mock:test"})], edges=[])
    service = TriggerService(runner, {"ingest": graph}, SECRET)

    with TestClient(create_trigger_app(service)) as client:
        body = b'{"event": "doc.created"}'
        # valid signature -> run starts and completes
        r = client.post("/v1/triggers/ingest", content=body,
                        headers={"X-Keel-Signature": _sign(body)})
        assert r.status_code == 200 and r.json()["status"] == "completed"
        # bad signature -> 401
        assert client.post("/v1/triggers/ingest", content=body,
                           headers={"X-Keel-Signature": "sha256=nope"}).status_code == 401
        # missing signature -> 401
        assert client.post("/v1/triggers/ingest", content=body).status_code == 401
        # unknown graph (valid sig) -> 404
        assert client.post("/v1/triggers/unknown", content=body,
                           headers={"X-Keel-Signature": _sign(body)}).status_code == 404
    await runner.close()
