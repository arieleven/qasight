from __future__ import annotations

from fastapi.testclient import TestClient

from testo_api.main import create_app


def test_health_endpoints() -> None:
    client = TestClient(create_app())
    live = client.get("/api/v1/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"

    ready = client.get("/api/v1/health/ready")
    assert ready.status_code in {200, 503}
    payload = ready.json()
    assert set(payload.keys()) == {"status", "checks"}
    assert set(payload["checks"]) == {"db", "repository", "disk"}


def test_readiness_is_ready_with_file_sqlite(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from testo_core.repository.db import reset_repository_cache
    from testo_core.repository.db_config import reset_engine_cache

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    reset_repository_cache()
    reset_engine_cache()
    try:
        ready = TestClient(create_app()).get("/api/v1/health/ready")
    finally:
        reset_repository_cache()
        reset_engine_cache()

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
