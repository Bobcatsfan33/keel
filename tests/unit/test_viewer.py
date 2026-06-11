"""Viewer API smoke test (skipped if the viewer extra isn't installed)."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from keel.authoring import Agent, Task, Crew  # noqa: E402
from keel.services.runner import Runner  # noqa: E402
from keel.services.model.handlers import MockModelPort  # noqa: E402
from keel.viewer.app import create_app  # noqa: E402


@pytest.mark.asyncio
async def test_viewer_serves_run(tmp_path):
    db = str(tmp_path / "k.db")
    blobs = str(tmp_path / "blobs")
    runner = await Runner.open(db_path=db, blob_dir=blobs,
                               model=MockModelPort(reply='{"summary": "hi"}'))
    a = Agent("r", goal="g")
    graph = Crew("vg", tasks=[Task("do it", agent=a, output_schema=None)]).compile()
    await runner.run(graph, run_id="vrun")
    await runner.close()

    with TestClient(create_app(db, blobs)) as client:
        runs = client.get("/api/runs").json()
        assert any(r["run_id"] == "vrun" for r in runs)
        detail = client.get("/api/runs/vrun").json()
        assert detail["status"] == "completed"
        assert detail["events"]
        cost = client.get("/api/runs/vrun/cost").json()
        assert "by_node" in cost
        assert client.get("/").status_code == 200  # SPA index
        assert client.get("/api/runs/nope").status_code == 404
