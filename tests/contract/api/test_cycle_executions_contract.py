"""Contract for ``/api/v1/cycle-executions/{id}`` status and SSE events."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from testo_api.cycle_execution_manager import CycleExecutionManager
from testo_api.main import create_app
from tests.fixtures.engine import use_echo_adapter, write_minimal_config


def _client(manager: CycleExecutionManager) -> TestClient:
    from testo_api.dependencies import get_cycle_execution_manager

    app = create_app()
    app.dependency_overrides[get_cycle_execution_manager] = lambda: manager
    return TestClient(app)


def _run(client: TestClient, config: Path) -> str:
    resp = client.post(
        "/api/v1/cycles/smoke/executions",
        json={"config_path": str(config), "report_db": False},
    )
    assert resp.status_code == 202, resp.text
    execution_id = resp.json()["execution_id"]
    deadline = time.monotonic() + 20
    while client.get(f"/api/v1/cycle-executions/{execution_id}").json()["status"] not in (
        "completed",
        "failed",
    ):
        assert time.monotonic() < deadline, "execution did not finish"
        time.sleep(0.05)
    return execution_id


def test_evicted_execution_is_404_and_points_to_run_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    config = write_minimal_config(tmp_path)
    client = _client(CycleExecutionManager(max_finished=1))

    first = _run(client, config)
    second = _run(client, config)

    assert client.get(f"/api/v1/cycle-executions/{second}").json()["status"] == "completed"
    for path in (f"/api/v1/cycle-executions/{first}", f"/api/v1/cycle-executions/{first}/events"):
        resp = client.get(path)
        assert resp.status_code == 404
        error = resp.json()["error"]
        assert error["code"] == "not_found"
        assert "last 1 finished" in error["message"]
        assert "/api/v1/runs" in error["message"]


def test_events_stream_closes_once_the_execution_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    config = write_minimal_config(tmp_path)
    client = _client(CycleExecutionManager())
    execution_id = _run(client, config)

    # A finished execution replays its events and then ends the response.
    with client.stream("GET", f"/api/v1/cycle-executions/{execution_id}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())

    events = [
        line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")
    ]
    assert events[0] == "plan_started"
    assert events[-1] == "plan_finished"


def test_events_stream_stops_at_its_own_run_in_the_shared_cycle_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    config = write_minimal_config(tmp_path)
    client = _client(CycleExecutionManager())
    first = _run(client, config)
    _run(client, config)  # appends to the same artifacts/smoke/events.ndjson

    with client.stream("GET", f"/api/v1/cycle-executions/{first}/events") as resp:
        body = "".join(resp.iter_text())

    events = [
        line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")
    ]
    assert events.count("plan_started") == 1
    assert events[-1] == "plan_finished"


def test_rerun_failed_of_a_run_without_failures_is_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from testo_core.services import run_failures

    monkeypatch.setattr(run_failures, "load_run_failures", lambda run_id: None)
    client = _client(CycleExecutionManager())
    resp = client.post("/api/v1/cycles/smoke/executions", json={"rerun_failed": "nope"})
    assert resp.status_code == 400
    assert "no run" in resp.text


def test_chosen_tests_reach_the_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from testo_core.services import cycle_run

    seen: dict[str, object] = {}
    real_run = cycle_run.CycleRunService.run

    def spy(self, *, cfg, plan, renderer, options=None):  # noqa: ANN001, ANN202
        seen["selection"] = options.selection if options else None
        return real_run(self, cfg=cfg, plan=plan, renderer=renderer, options=options)

    monkeypatch.setattr(cycle_run.CycleRunService, "run", spy)
    use_echo_adapter(monkeypatch)
    config = write_minimal_config(tmp_path)
    client = _client(CycleExecutionManager())
    resp = client.post(
        "/api/v1/cycles/smoke/executions",
        json={"config_path": str(config), "report_db": False, "tests": ["test_a"]},
    )
    assert resp.status_code == 202, resp.text
    execution_id = resp.json()["execution_id"]
    deadline = time.monotonic() + 20
    while client.get(f"/api/v1/cycle-executions/{execution_id}").json()["status"] not in (
        "completed",
        "failed",
    ):
        assert time.monotonic() < deadline, "execution did not finish"
        time.sleep(0.05)
    selection = seen["selection"]
    assert selection is not None
    assert [t.name for t in selection.tests] == ["test_a"]  # type: ignore[attr-defined]
