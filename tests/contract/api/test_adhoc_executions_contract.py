"""Contract for ``POST /api/v1/adhoc-executions`` (one framework, no cycle)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient

from qasight.config.errors import ConfigValidationError
from qasight_api.main import create_app


@dataclass
class _FakeState:
    execution_id: str
    status: str = "queued"
    lock: threading.Lock = field(default_factory=threading.Lock)


class _FakeManager:
    def __init__(self, *, status: str = "running", error: Exception | None = None) -> None:
        self.state = _FakeState(execution_id="exec-1", status=status)
        self.error = error
        self.calls: list[dict] = []

    def create_adhoc_execution(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.state


def _client(manager: _FakeManager) -> TestClient:
    from qasight_api.dependencies import get_cycle_execution_manager

    app = create_app()
    app.dependency_overrides[get_cycle_execution_manager] = lambda: manager
    return TestClient(app)


def test_create_adhoc_execution_contract_shape() -> None:
    manager = _FakeManager()
    resp = _client(manager).post(
        "/api/v1/adhoc-executions",
        json={"framework": "pytest", "target_repo": ".", "args": ["-q"]},
    )
    assert resp.status_code == 202
    payload = resp.json()
    assert set(payload.keys()) == {"execution_id", "status", "events_url", "summary_url"}
    assert payload["status"] == "queued"
    # Ad-hoc runs share the cycle execution resource: one status/events API for both.
    assert payload["events_url"].endswith("/api/v1/cycle-executions/exec-1/events")
    assert payload["summary_url"].endswith("/api/v1/cycle-executions/exec-1")
    call = manager.calls[0]
    assert call["framework"] == "pytest"
    assert call["target_repo"] == Path(".")
    assert call["args"] == ["-q"]
    assert call["persist"] is True


def test_create_adhoc_execution_status_is_queued_even_after_fast_completion() -> None:
    resp = _client(_FakeManager(status="completed")).post(
        "/api/v1/adhoc-executions", json={"framework": "behave", "target_repo": "."}
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"


def test_create_adhoc_execution_rejects_unknown_framework() -> None:
    resp = _client(_FakeManager()).post(
        "/api/v1/adhoc-executions", json={"framework": "locust", "target_repo": "."}
    )
    assert resp.status_code == 422


def test_create_adhoc_execution_maps_invalid_stage_to_400() -> None:
    manager = _FakeManager(error=ConfigValidationError("target_repo is not a directory: /nope"))
    resp = _client(manager).post(
        "/api/v1/adhoc-executions", json={"framework": "pytest", "target_repo": "/nope"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_input"


def test_create_adhoc_execution_conflicts_while_another_adhoc_runs() -> None:
    manager = _FakeManager(error=RuntimeError("cycle 'adhoc' already running (execution_id=x)"))
    resp = _client(manager).post(
        "/api/v1/adhoc-executions", json={"framework": "pytest", "target_repo": "."}
    )
    assert resp.status_code == 409


def test_legacy_execution_routes_are_gone() -> None:
    client = _client(_FakeManager())
    assert client.post("/api/v1/executions", json={"runs": []}).status_code == 404
    assert client.get("/api/v1/executions/exec-1").status_code == 404
