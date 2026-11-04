"""Contract for the quarantine kept on the server: ``/api/v1/quarantine``, the writes'
auth, ``quarantined`` on ``/analytics/flaky``, and ``POST /api/v1/ingest`` honouring it."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight.repository.db import reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight.services import flaky_service
from qasight.services.flaky_service import FlakyReport, FlakyTest
from qasight_api.login_throttle import login_throttle
from qasight_api.main import create_app
from qasight_api.security import account_store

_TOKEN = "s3cret-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_PASSWORD = "correct horse battery"
_FAILING = b"""<testsuite name="pay"><testcase classname="pay" name="test_ok"/>
<testcase classname="pay" name="test_flaky_refund"><failure message="boom">trace</failure>
</testcase></testsuite>"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    monkeypatch.setenv("TESTO_QUARANTINE_RELEASE_AFTER", "0")
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


def test_add_list_and_release(client: TestClient) -> None:
    body = {"cycle": "web", "test": "test_flaky_refund", "reason": "races", "owner": "@pay"}
    # A write: the API token is needed, like triage.
    assert client.put("/api/v1/quarantine", json=body).status_code == 401

    resp = client.put("/api/v1/quarantine", json=body, headers=_AUTH)
    assert resp.status_code == 200, resp.text
    added = resp.json()
    assert (added["test"], added["is_glob"], added["reason"], added["added_by"]) == (
        "test_flaky_refund",
        False,
        "races",
        None,
    )
    assert isinstance(added["added_at"], float)
    client.put("/api/v1/quarantine", json={"cycle": "web", "test": "pay#*"}, headers=_AUTH)

    listed = client.get("/api/v1/quarantine", params={"cycle": "web"}).json()
    assert [(i["test"], i["is_glob"]) for i in listed["items"]] == [
        ("pay#*", True),
        ("test_flaky_refund", False),
    ]
    assert listed["release_after"] == 0
    assert client.get("/api/v1/quarantine", params={"cycle": "api"}).json()["items"] == []

    params = {"cycle": "web", "test": "test_flaky_refund"}
    assert client.delete("/api/v1/quarantine", params=params).status_code == 401
    assert client.delete("/api/v1/quarantine", params=params, headers=_AUTH).status_code == 204
    assert client.delete("/api/v1/quarantine", params=params, headers=_AUTH).status_code == 404


def test_blank_tests_are_refused(client: TestClient) -> None:
    body = {"cycle": "web", "test": "   "}
    assert client.put("/api/v1/quarantine", json=body, headers=_AUTH).status_code == 422
    assert client.put("/api/v1/quarantine", json={"cycle": "web"}, headers=_AUTH).status_code == 422


def test_server_ingest_honours_the_stored_list(client: TestClient) -> None:
    def upload() -> dict[str, object]:
        resp = client.post(
            "/api/v1/ingest",
            data={"cycle": "web"},
            files=[("files", ("junit.xml", _FAILING, "application/xml"))],
            headers=_AUTH,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    # qasight ingest --server exits with this exit_code, so the job turns green too.
    assert upload()["exit_code"] == 1
    body = {"cycle": "web", "test": "test_flaky_refund"}
    assert client.put("/api/v1/quarantine", json=body, headers=_AUTH).status_code == 200
    second = upload()
    assert (second["exit_code"], second["failed"]) == (0, 1)  # muted, still reported


def test_flaky_items_say_whether_they_are_quarantined(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def flaky(name: str) -> FlakyTest:
        return FlakyTest(
            key=f"k-{name}",
            name=name,
            group="pay",
            runs=4,
            passed=2,
            failed=2,
            flips=3,
            flip_rate=1.0,
            last_status="failed",
            history=("passed", "failed", "passed", "failed"),
            full_name=f"pay#{name}",
        )

    report = FlakyReport(
        cycle="web",
        window=20,
        runs_analyzed=4,
        tests_analyzed=3,
        flaky=(flaky("test_a"), flaky("test_b"), flaky("test_c")),
    )
    monkeypatch.setattr("qasight_api.routes.analytics.find_flaky_tests", lambda **_: report)
    monkeypatch.setattr(flaky_service, "_cycle_runs", lambda cycle: [])
    client.put("/api/v1/quarantine", json={"cycle": "web", "test": "test_a"}, headers=_AUTH)
    client.put("/api/v1/quarantine", json={"cycle": "web", "test": "pay#test_b*"}, headers=_AUTH)

    items = client.get("/api/v1/analytics/flaky", params={"cycle": "web"}).json()["items"]
    state = {i["name"]: (i["quarantined"], i["quarantine_entry"]) for i in items}
    assert state == {
        "test_a": (True, "test_a"),
        "test_b": (True, None),  # a glob matches; the UI can't release one test from it
        "test_c": (False, None),
    }


@pytest.fixture
def accounts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TESTO_AUTH", "accounts")
    account_store.cache_clear()
    login_throttle.reset()
    for name, role in (("vera", "viewer"), ("rui", "runner")):
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


def test_writes_need_the_runner_role_and_are_audited(accounts: None) -> None:
    body = {"cycle": "web", "test": "test_flaky_refund"}
    viewer = _signed_in("vera")
    assert viewer.get("/api/v1/quarantine").status_code == 200
    assert viewer.put("/api/v1/quarantine", json=body).status_code == 403

    runner = _signed_in("rui")
    resp = runner.put("/api/v1/quarantine", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["added_by"] == "rui"
    params = {"cycle": "web", "test": "test_flaky_refund"}
    assert viewer.delete("/api/v1/quarantine", params=params).status_code == 403
    assert runner.delete("/api/v1/quarantine", params=params).status_code == 204

    audited = [(e.username, e.method, e.path) for e in account_store().recent(10)]
    assert ("rui", "PUT", "/api/v1/quarantine") in audited
    assert ("rui", "DELETE", "/api/v1/quarantine") in audited
