"""Contract for deleting chosen runs (``POST /api/v1/runs:delete``), the run settings
(``GET /api/v1/run-settings``) and stopping a run (``POST .../cancel``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight.repository.db import get_repository, get_test_result_store, reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight.repository.models import RunStatus
from qasight_api.dependencies import get_cycle_execution_manager
from qasight_api.login_throttle import login_throttle
from qasight_api.main import create_app
from qasight_api.security import account_store

pytestmark = pytest.mark.tier_fast

_TOKEN = "s3cret-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_PASSWORD = "correct horse battery"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    for name in ("TESTO_AUTH", "TESTO_PROTECT_READS", "TESTO_CORS_ORIGINS", "TESTO_UI_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("qasight.paths.ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr("qasight.paths.STATIC_HISTORY_ROOT", tmp_path / "static" / "history")
    monkeypatch.chdir(tmp_path)  # no qasight.yaml here
    reset_engine_cache()
    reset_repository_cache()
    yield TestClient(create_app())
    reset_repository_cache()
    reset_engine_cache()


def _record(status: RunStatus = RunStatus.COMPLETED) -> str:
    record = get_repository().create_run(status=status, metadata={"plan": "unit"})
    run_id = str(record.id)
    get_test_result_store().replace(run_id, {"t": {"name": "t", "status": "passed"}})
    return run_id


def test_delete_removes_the_chosen_runs_only(client: TestClient, tmp_path: Path) -> None:
    gone, kept, running = _record(), _record(), _record(RunStatus.RUNNING)
    (tmp_path / "static" / "history" / gone).mkdir(parents=True)
    body = {"run_ids": [gone, running, "not-a-run"]}

    assert client.post("/api/v1/runs:delete", json=body).status_code == 401  # a write

    resp = client.post("/api/v1/runs:delete", json=body, headers=_AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "deleted": [gone],
        "test_rows_deleted": 1,
        "snapshots_deleted": 1,
        "skipped_running": [running],
        "not_found": ["not-a-run"],
        "errors": [],
    }
    assert client.get(f"/api/v1/runs/{gone}").status_code == 404
    assert client.get(f"/api/v1/runs/{kept}").status_code == 200
    assert get_repository().get_run(running) is not None
    assert not (tmp_path / "static" / "history" / gone).exists()


def test_delete_needs_at_least_one_id(client: TestClient) -> None:
    resp = client.post("/api/v1/runs:delete", json={"run_ids": []}, headers=_AUTH)
    assert resp.status_code == 422


def test_run_settings_without_a_config(client: TestClient) -> None:
    resp = client.get("/api/v1/run-settings")
    assert resp.status_code == 200
    assert resp.json() == {
        "config_found": False,
        "retention": {"days": None, "runs_per_cycle": None, "enabled": False},
        "cleanup_interval_hours": None,
        "default_stage_timeout_s": None,
    }


def test_run_settings_read_qasight_yaml(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "qasight.yaml").write_text(
        "version: 1\n"
        "defaults:\n  timeout_s: 900\n"
        "history:\n  retention:\n    days: 30\n    runs_per_cycle: 50\n"
        "cycles:\n  unit:\n    stages:\n      - name: unit\n        equipment: pytest\n",
        encoding="utf-8",
    )
    got = client.get("/api/v1/run-settings").json()
    assert got == {
        "config_found": True,
        "retention": {"days": 30, "runs_per_cycle": 50, "enabled": True},
        "cleanup_interval_hours": 6.0,
        "default_stage_timeout_s": 900.0,
    }


def test_cancel_an_unknown_execution_is_404(client: TestClient) -> None:
    resp = client.post("/api/v1/cycle-executions/nope/cancel", headers=_AUTH)
    assert resp.status_code == 404


def test_cancel_a_queued_execution(client: TestClient) -> None:
    import threading

    from qasight_api.run_queue import RunRequest

    manager = get_cycle_execution_manager()
    release = threading.Event()

    def blocked():  # noqa: ANN202
        assert release.wait(10)
        raise RuntimeError("done")

    def request(cycle: str) -> RunRequest:
        return RunRequest(kind="cycle", cycle=cycle, persist=False, report_db=False)

    busy = [manager._start(request(f"busy-{i}"), blocked) for i in range(manager.max_concurrent)]
    waiting = manager._start(request("waiting"), blocked)
    try:
        assert (
            client.post(f"/api/v1/cycle-executions/{waiting.execution_id}/cancel").status_code
            == 401
        )
        resp = client.post(f"/api/v1/cycle-executions/{waiting.execution_id}/cancel", headers=_AUTH)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
        assert resp.json()["error"] == "Stopped before it started."
    finally:
        release.set()
    for state in busy:
        while not state.done:
            threading.Event().wait(0.05)


@pytest.fixture
def accounts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TESTO_AUTH", "accounts")
    account_store.cache_clear()
    login_throttle.reset()
    for name, role in (("rui", "runner"), ("ada", "admin")):
        account_store().add(name, _PASSWORD, role)
    yield
    account_store.cache_clear()


def _signed_in(username: str) -> TestClient:
    client = TestClient(create_app())
    resp = client.post(
        "/api/v1/auth/login",
        data={"username": username, "token": _PASSWORD, "next": "/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return client


def test_deleting_runs_is_an_admins_call_and_is_audited(accounts: None) -> None:
    run_id = _record()
    body = {"run_ids": [run_id]}
    assert _signed_in("rui").post("/api/v1/runs:delete", json=body).status_code == 403
    resp = _signed_in("ada").post("/api/v1/runs:delete", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == [run_id]
    audited = [(e.username, e.method, e.path) for e in account_store().recent(10)]
    assert ("ada", "POST", "/api/v1/runs:delete") in audited
