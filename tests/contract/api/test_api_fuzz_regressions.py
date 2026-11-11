"""Requests Schemathesis found answered with a 500 (2026-11-11); each now gets a 422."""

from __future__ import annotations

from fastapi.testclient import TestClient

from qasight_api.main import create_app


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_huge_dashboard_limits_are_rejected_not_overflowed() -> None:
    client = _client()
    too_big = 2**63
    assert client.get(f"/api/v1/dashboard/overview?recent_limit={too_big}").status_code == 422
    assert client.get(f"/api/v1/dashboard/runs/recent?limit={too_big}").status_code == 422


def test_validation_error_with_a_raw_body_is_a_422() -> None:
    response = _client().post(
        "/api/v1/runs:delete",
        content=b'{"run_ids": [""]}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_validation_error_with_a_bad_upload_is_a_422() -> None:
    response = _client().post("/api/v1/ingest", data={"files": "{}", "cycle": "", "stage": "junit"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
