"""Live results: ``pytest --qasight-server`` sends each test to ``/api/v1/live-runs`` while it runs."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from qasight.repository.db_config import reset_engine_cache
from qasight.services.live_client import LiveReporter
from qasight_api.live_runs import FINISHED_KEEP_S, STALE_AFTER_S, LiveRunStore, live_runs
from qasight_api.main import create_app

_TOKEN = "live-token"


@pytest.fixture
def server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db = tmp_path / "history.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    monkeypatch.delenv("TESTO_PROTECT_READS", raising=False)
    monkeypatch.delenv("TESTO_AUTH", raising=False)
    reset_engine_cache()
    live_runs.clear()
    yield db
    live_runs.clear()
    reset_engine_cache()


def _authed() -> TestClient:
    client = TestClient(create_app())
    client.headers["Authorization"] = f"Bearer {_TOKEN}"
    return client


def test_a_session_reports_progress_and_failures(server_env: Path) -> None:
    client = _authed()
    started = client.post("/api/v1/live-runs", json={"cycle": "shop", "total": 3, "branch": "main"})
    assert started.status_code == 201
    sid = started.json()["id"]
    results = [
        {"test": "t::ok", "outcome": "passed"},
        {"test": "t::bad", "outcome": "failed", "message": "assert 1 == 2"},
    ]
    assert (
        client.post(f"/api/v1/live-runs/{sid}/results", json={"results": results}).status_code
        == 204
    )
    item = client.get("/api/v1/live-runs").json()["items"][0]
    assert (item["cycle"], item["total"], item["done"], item["passed"], item["failed"]) == (
        "shop",
        3,
        2,
        1,
        1,
    )
    assert item["failures"] == [{"test": "t::bad", "message": "assert 1 == 2"}]
    assert item["current"] == "t::bad" and item["finished"] is False
    assert client.post(f"/api/v1/live-runs/{sid}/finish").status_code == 204
    assert client.get("/api/v1/live-runs").json()["items"][0]["finished"] is True


def test_writes_need_the_token_and_unknown_sessions_404(server_env: Path) -> None:
    anonymous = TestClient(create_app())
    assert anonymous.post("/api/v1/live-runs", json={"cycle": "shop"}).status_code == 401
    client = _authed()
    assert client.post("/api/v1/live-runs/nope/finish").status_code == 404
    body = {"results": [{"test": "t", "outcome": "passed"}]}
    assert client.post("/api/v1/live-runs/nope/results", json=body).status_code == 404
    bad = {"results": [{"test": "t", "outcome": "exploded"}]}
    sid = client.post("/api/v1/live-runs", json={"cycle": "shop"}).json()["id"]
    assert client.post(f"/api/v1/live-runs/{sid}/results", json=bad).status_code == 422


def test_store_forgets_finished_and_silent_sessions() -> None:
    store = LiveRunStore()
    done = store.start("a", now=0)
    store.finish(done.id, now=10)
    silent = store.start("b", now=0)
    alive = store.start("c", now=0)
    store.add_results(alive.id, [("t", "passed", None)], now=STALE_AFTER_S)
    ids = {s.id for s in store.active(now=STALE_AFTER_S + 1)}
    assert done.id not in ids and silent.id not in ids and alive.id in ids
    assert FINISHED_KEEP_S < STALE_AFTER_S


def test_reporter_turns_itself_off_on_the_first_error(server_env: Path) -> None:
    client = TestClient(create_app())  # no token: the server refuses
    reporter = LiveReporter(server="http://testserver", token=None, session=client)
    reporter.start("shop")
    reporter.add("t::ok", "passed")
    reporter.finish()
    assert reporter.disabled and "401" in (reporter.error or "")
    assert live_runs.active() == []


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_TESTS = """
import time

def test_ok():
    assert True

def test_bad():
    assert 1 == 2

def test_slow():
    time.sleep(0.1)
"""


def test_pytest_plugin_streams_results_then_uploads(server_env: Path, tmp_path: Path) -> None:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        root = tmp_path / "shop"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_demo.py").write_text(_TESTS, encoding="utf-8")
        (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TESTO_", "PYTEST_"))}
        for ci_var in ("CI", "GITHUB_ACTIONS", "GITLAB_CI"):
            env.pop(ci_var, None)
        env.update(
            {"TESTO_API_TOKEN": _TOKEN, "DATABASE_URL": f"sqlite:///{tmp_path / 'client.db'}"}
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--qasight-cycle",
                "shop",
                "--qasight-server",
                f"http://127.0.0.1:{port}",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "live results stopped" not in result.stdout
        sessions = live_runs.active()
        assert len(sessions) == 1, result.stdout
        live = sessions[0]
        assert (live.cycle, live.total, live.done) == ("shop", 3, 3)
        assert live.counts["failed"] == 1 and live.finished_at is not None
        assert (
            live.failures[0].test.endswith("test_bad")
            and "assert 1 == 2" in live.failures[0].message
        )
        assert "recorded this session" in result.stdout
    finally:
        server.should_exit = True
        thread.join(timeout=10)
