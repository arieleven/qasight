"""Contract for failure causes: ``/api/v1/triage`` and ``triage`` on a run's failing tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight.repository.db_config import reset_engine_cache
from qasight_api.main import create_app

_TOKEN = "s3cret-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    for name in ("TESTO_AUTH", "TESTO_PROTECT_READS", "TESTO_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    reset_engine_cache()
    yield TestClient(create_app())
    reset_engine_cache()


def test_mark_list_and_clear_a_cause(client: TestClient) -> None:
    body = {
        "cycle": "api",
        "message": "ConnectionRefusedError: port 5432",
        "cause": "environment",
        "note": "DB container starts late",
        "run_id": "run-1",
        "test_key": "k1",
    }
    # A write: the API token is needed.
    assert client.put("/api/v1/triage", json=body).status_code == 401

    resp = client.put("/api/v1/triage", json=body, headers=_AUTH)
    assert resp.status_code == 200, resp.text
    marked = resp.json()
    assert marked["signature"] == "connectionrefusederror: port …"
    assert (marked["cause"], marked["marked_by"]) == ("environment", None)
    assert isinstance(marked["marked_at"], float)

    listed = client.get("/api/v1/triage", params={"cycle": "api"}).json()["causes"]
    assert [c["note"] for c in listed] == ["DB container starts late"]
    assert client.get("/api/v1/triage", params={"cycle": "web"}).json() == {"causes": []}

    params = {"cycle": "api", "signature": marked["signature"]}
    assert client.delete("/api/v1/triage", params=params, headers=_AUTH).status_code == 204
    assert client.delete("/api/v1/triage", params=params, headers=_AUTH).status_code == 404


def test_unknown_causes_are_refused(client: TestClient) -> None:
    body = {"cycle": "api", "message": "boom", "cause": "gremlins"}
    assert client.put("/api/v1/triage", json=body, headers=_AUTH).status_code == 422
