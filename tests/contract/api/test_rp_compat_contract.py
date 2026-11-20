"""ReportPortal-compatible reporting: an agent's calls to ``/api/{v1,v2}/{project}/...`` become a run.

The call sequence and bodies follow what ``pytest-reportportal`` 5.6 and
``@reportportal/agent-js-playwright`` 5.5 send (both were run against this
endpoint while it was written).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight.repository.db_config import reset_engine_cache
from qasight.services.rp_compat import cycle_for_launch, failure_message, parse_time_ms
from qasight_api.live_runs import live_runs
from qasight_api.main import create_app

_TOKEN = "rp-token"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    monkeypatch.delenv("TESTO_PROTECT_READS", raising=False)
    monkeypatch.delenv("TESTO_AUTH", raising=False)
    monkeypatch.chdir(tmp_path)
    reset_engine_cache()
    live_runs.clear()
    app_client = TestClient(create_app())
    app_client.headers["Authorization"] = f"Bearer {_TOKEN}"
    yield app_client
    live_runs.clear()
    reset_engine_cache()


def _start(client: TestClient, path: str, body: dict) -> str:
    resp = client.post(path, json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _finish(client: TestClient, path: str, body: dict) -> None:
    resp = client.put(path, json=body)
    assert resp.status_code == 200, resp.text


def test_a_launch_becomes_a_run_with_failures_retries_and_attachments(client: TestClient) -> None:
    base = "/api/v2/shop"
    launch = _start(
        client,
        f"{base}/launch",
        {
            "name": "Smoke Tests",
            "startTime": "2026-11-16T10:00:00.000000Z",
            "attributes": [
                {"key": "branch", "value": "main"},
                {"key": "browser", "value": "chromium"},
                {"value": "smoke"},
                {"key": "agent", "value": "pytest-reportportal|5.6.13", "system": True},
            ],
        },
    )
    suite = _start(
        client,
        f"{base}/item",
        {"launchUuid": launch, "name": "test_shop.py", "type": "SUITE", "startTime": 1},
    )
    ok = _start(
        client,
        f"{base}/item/{suite}",
        {
            "launchUuid": launch,
            "name": "test_ok",
            "type": "STEP",
            "codeRef": "test_shop.py:test_ok",
            "startTime": 2,
        },
    )
    step = _start(
        client,
        f"{base}/item/{ok}",
        {
            "launchUuid": launch,
            "name": "open cart",
            "type": "STEP",
            "hasStats": False,
            "startTime": 3,
        },
    )
    _finish(client, f"{base}/item/{step}", {"launchUuid": launch, "status": "PASSED", "endTime": 4})
    _finish(client, f"{base}/item/{ok}", {"launchUuid": launch, "status": "PASSED", "endTime": 5})

    bad = _start(
        client,
        f"{base}/item/{suite}",
        {
            "launchUuid": launch,
            "name": "test_bad",
            "type": "STEP",
            "codeRef": "test_shop.py:test_bad",
            "startTime": 6,
        },
    )
    _finish(client, f"{base}/item/{bad}", {"launchUuid": launch, "status": "FAILED", "endTime": 7})

    # A test that failed once and passed on its retry.
    first = _start(
        client,
        f"{base}/item/{suite}",
        {
            "launchUuid": launch,
            "name": "test_flaky",
            "type": "STEP",
            "codeRef": "test_shop.py:test_flaky",
            "startTime": 8,
        },
    )
    _finish(
        client, f"{base}/item/{first}", {"launchUuid": launch, "status": "FAILED", "endTime": 9}
    )
    second = _start(
        client,
        f"{base}/item/{suite}",
        {
            "launchUuid": launch,
            "name": "test_flaky",
            "type": "STEP",
            "codeRef": "test_shop.py:test_flaky",
            "startTime": 10,
            "retry": True,
        },
    )
    _finish(
        client, f"{base}/item/{second}", {"launchUuid": launch, "status": "PASSED", "endTime": 11}
    )
    _finish(client, f"{base}/item/{suite}", {"launchUuid": launch, "endTime": 12})

    # Logs: a batch with an attachment, the way the Python client sends them at the end.
    entries = [
        {
            "launchUuid": launch,
            "itemUuid": bad,
            "time": 6,
            "level": "ERROR",
            "message": "def test_bad():\nE   AssertionError: price mismatch\nE   assert 1 == 2",
            "file": {"name": "page.png"},
        },
        {
            "launchUuid": launch,
            "itemUuid": first,
            "time": 8,
            "level": "ERROR",
            "message": "TimeoutError: slow",
        },
        {
            "launchUuid": launch,
            "itemUuid": ok,
            "time": 2,
            "level": "INFO",
            "message": "checking the cart",
        },
    ]
    resp = client.post(
        f"{base}/log",
        files=[
            ("json_request_part", (None, json.dumps(entries), "application/json")),
            ("file", ("page.png", b"\x89PNG fake", "image/png")),
        ],
    )
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["responses"]) == 3

    live = client.get("/api/v1/live-runs").json()["items"][0]
    assert (live["cycle"], live["passed"], live["failed"], live["branch"]) == (
        "Smoke-Tests",
        2,
        2,
        "main",
    )

    done = client.put(f"{base}/launch/{launch}/finish", json={"endTime": "2026-11-16T10:01:00Z"})
    assert done.status_code == 200, done.text
    assert done.json()["id"] == launch

    runs = client.get("/api/v1/runs?limit=1").json()["items"]
    assert len(runs) == 1
    run = runs[0]
    assert (run["cycle"], run["total_tests"], run["passed"], run["failed"]) == (
        "Smoke-Tests",
        3,
        2,
        1,
    )
    assert (run["branch"], run["environment"]) == ("main", "browser=chromium")

    failures = client.get(f"/api/v1/runs/{run['run_id']}/failures").json()["tests"]
    assert [(t["name"], t["message"]) for t in failures] == [
        ("test_bad", "AssertionError: price mismatch")
    ]

    info = client.get(f"{base}/launch/uuid/{launch}").json()
    assert info["status"] == "FINISHED" and info["qasightRunId"] == run["run_id"]
    # A second finish (an agent retrying) answers again without recording twice.
    assert client.put(f"{base}/launch/{launch}/finish", json={}).status_code == 200
    assert len(client.get("/api/v1/runs").json()["items"]) == 1


def test_results_files_carry_steps_flaky_and_labels(client: TestClient, tmp_path: Path) -> None:
    from qasight.repository.models import RpItem, RpLaunch, RpLog
    from qasight.services.rp_compat import build_results

    launch = RpLaunch(
        uuid="L",
        project="p",
        name="n",
        cycle="n",
        attributes=[{"key": "agent", "value": "agent-js-playwright|5.5.0", "system": True}],
    )
    items = [
        RpItem(uuid="s", launch_uuid="L", name="Cart", type="SUITE", seq=1),
        RpItem(uuid="t1", launch_uuid="L", parent_uuid="s", name="total", seq=2, status="FAILED"),
        RpItem(
            uuid="t2",
            launch_uuid="L",
            parent_uuid="s",
            name="total",
            seq=3,
            status="PASSED",
            retry=True,
            attributes=[
                {"key": "team", "value": "web", "system": False},
                {"key": None, "value": "slow", "system": False},
            ],
        ),
        RpItem(
            uuid="st",
            launch_uuid="L",
            parent_uuid="t2",
            name="Expect",
            has_stats=False,
            seq=4,
            status="PASSED",
        ),
    ]
    logs = [
        RpLog(
            uuid="g",
            launch_uuid="L",
            item_uuid="t1",
            level="ERROR",
            message="Error: expected 3\n  at x",
        )
    ]
    built = build_results(launch, items, logs)
    assert len(built.results) == 1
    result = built.results[0]
    assert result["status"] == "passed"
    assert result["statusDetails"]["flaky"] is True
    assert result["statusDetails"]["message"] == "Passed on retry. Error: expected 3"
    assert [s["name"] for s in result["steps"]] == ["Expect"]
    labels = {(lab["name"], lab["value"]) for lab in result["labels"]}
    assert {
        ("framework", "agent-js-playwright"),
        ("parentSuite", "Cart"),
        ("team", "web"),
        ("tag", "slow"),
    } <= labels


def test_unknown_launch_and_item_answer_like_reportportal(client: TestClient) -> None:
    resp = client.post("/api/v1/shop/item", json={"launchUuid": "nope", "name": "x"})
    assert resp.status_code == 404 and resp.json()["errorCode"] == 40422
    assert client.put("/api/v1/shop/item/nope", json={"status": "PASSED"}).status_code == 404
    assert client.post("/api/v1/shop/item", json={"name": "x"}).status_code == 400


def test_writes_need_the_token(client: TestClient) -> None:
    anonymous = TestClient(create_app())
    assert anonymous.post("/api/v1/shop/launch", json={"name": "x"}).status_code == 401


def test_qasight_routes_still_win_their_paths(client: TestClient) -> None:
    assert client.get("/api/v1/live-runs").status_code == 200
    assert client.get("/api/info").json()["build"]["version"]


def test_helpers() -> None:
    assert parse_time_ms(1_700_000_000_000) == 1_700_000_000_000
    assert parse_time_ms("1700000000000") == 1_700_000_000_000
    assert parse_time_ms("2023-11-14T22:13:20.000000Z") == 1_700_000_000_000
    assert parse_time_ms(1_700_000_000) == 1_700_000_000_000
    assert cycle_for_launch("Smoke Tests") == "Smoke-Tests"
    assert cycle_for_launch("  ") == "reportportal"
    assert (
        failure_message("java.lang.AssertionError: no\n\tat A.b(A.java:1)")
        == "java.lang.AssertionError: no"
    )
    assert failure_message("Traceback\n  File x\nValueError: boom") == "ValueError: boom"


def test_an_oversized_body_is_refused_before_it_is_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("qasight_api.routes.rp_compat._MAX_JSON_BYTES", 1000)
    big = b'{"name": "' + b"x" * 5000 + b'"}'
    resp = client.post("/api/v2/shop/launch", content=big)
    assert resp.status_code == 413

    def chunks():  # no Content-Length: the running cap refuses it
        for _ in range(10):
            yield b"x" * 500

    resp = client.post("/api/v2/shop/launch", content=chunks())
    assert resp.status_code == 413
