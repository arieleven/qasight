"""``qasight quarantine list|add|remove`` and how ``qasight flaky`` shows the stored list."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.repository.db_config import reset_engine_cache
from qasight.services.flaky_service import FlakyReport, FlakyTest
from qasight.services.quarantine_store import QuarantineStore

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.fixture(autouse=True)
def _history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    for name in ("TESTO_SERVER_URL", "TESTO_AUTH", "TESTO_QUARANTINE_RELEASE_AFTER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no qasight.yaml
    reset_engine_cache()
    yield
    reset_engine_cache()


def _testo(*args: str) -> Any:
    return CliRunner().invoke(app, list(args))


def test_add_list_and_remove() -> None:
    added = _testo("quarantine", "add", "--cycle", "web", "test_flaky_refund", "--reason", "#42")
    assert added.exit_code == 0, added.output
    assert "Quarantined test_flaky_refund in cycle web." in added.output
    assert "released after 10 clean runs" in added.output
    assert _testo("quarantine", "add", "--cycle", "web", "pay#*").exit_code == 0

    listed = _testo("quarantine", "list", "--cycle", "web")
    assert listed.exit_code == 0
    assert "web  test_flaky_refund  (reason: #42" in listed.output
    assert "globs stay until removed" in listed.output
    as_json = json.loads(_testo("quarantine", "list", "--json").output)
    assert [i["test"] for i in as_json["items"]] == ["pay#*", "test_flaky_refund"]
    assert as_json["release_after"] == 10

    removed = _testo("quarantine", "remove", "--cycle", "web", "test_flaky_refund")
    assert removed.exit_code == 0 and "Released test_flaky_refund" in removed.output
    again = _testo("quarantine", "remove", "--cycle", "web", "test_flaky_refund")
    assert again.exit_code == 1
    assert "isn't quarantined" in again.output
    assert [e.test for e in QuarantineStore().list("web")] == ["pay#*"]


def test_empty_list_and_bad_input() -> None:
    assert (
        "No quarantined tests in cycle web."
        in _testo("quarantine", "list", "--cycle", "web").output
    )
    assert _testo("quarantine", "add", "--cycle", "web", "  ").exit_code == 2


def test_server_mode_calls_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    calls: list[tuple[str, str, dict[str, Any], Any, dict[str, str]]] = []

    class _Resp:
        def __init__(self, status: int, body: Any) -> None:
            self.status_code, self._body = status, body
            self.content = b"" if body is None else b"x"

        def json(self) -> Any:
            return self._body

    def fake(method: str, url: str, **kw: Any) -> _Resp:
        calls.append((method, url, kw["params"], kw["json"], kw["headers"]))
        if method == "GET":
            return _Resp(200, {"items": [{"cycle": "web", "test": "t1"}], "release_after": 5})
        return _Resp(204 if method == "DELETE" else 200, None if method == "DELETE" else {})

    monkeypatch.setattr(requests, "request", fake)
    monkeypatch.setenv("TESTO_API_TOKEN", "tok")
    server = "https://qasight.example"
    assert _testo("quarantine", "add", "--cycle", "web", "t1", "--server", server).exit_code == 0
    listed = _testo("quarantine", "list", "--server", server)
    assert "web  t1" in listed.output and "after 5 clean runs" in listed.output
    assert _testo("quarantine", "remove", "--cycle", "web", "t1", "--server", server).exit_code == 0
    assert [(c[0], c[1]) for c in calls] == [
        ("PUT", f"{server}/api/v1/quarantine"),
        ("GET", f"{server}/api/v1/quarantine"),
        ("DELETE", f"{server}/api/v1/quarantine"),
    ]
    assert calls[0][3]["test"] == "t1" and calls[0][4] == {"Authorization": "Bearer tok"}
    assert calls[2][2] == {"cycle": "web", "test": "t1"}
    # Nothing went to this machine's list.
    assert QuarantineStore().list() == []


def _flaky(name: str) -> FlakyTest:
    return FlakyTest(
        key=f"k-{name}",
        name=name,
        group="tests.api",
        runs=3,
        passed=2,
        failed=1,
        flips=2,
        flip_rate=1.0,
        last_status="passed",
        history=("passed", "failed", "passed"),
    )


def test_flaky_marks_stored_entries_and_suggests_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flaky = (_flaky("test_stored"), _flaky("test_open"))
    monkeypatch.setattr(
        "qasight.services.flaky_service.find_flaky_tests",
        lambda *, cycle, window: FlakyReport(
            cycle=cycle, window=window, runs_analyzed=3, tests_analyzed=5, flaky=flaky
        ),
    )
    QuarantineStore().add(cycle="web", test="test_stored")

    result = _testo("flaky", "--cycle", "web", "--json")
    marks = {t["name"]: t["quarantined"] for t in json.loads(result.stdout)["flaky"]}
    assert marks == {"test_stored": True, "test_open": False}

    text = CliRunner().invoke(app, ["flaky", "--cycle", "web"], env={"COLUMNS": "200"}).output
    assert 'qasight quarantine add --cycle web "test_open"' in text
    assert 'qasight quarantine add --cycle web "test_stored"' not in text
    # Config users still get the yaml snippet.
    assert "qasight.yaml" in text and '- test: "test_open"' in text


def test_add_on_a_local_history_says_how_to_quarantine_in_ci_too() -> None:
    import yaml

    result = _testo("quarantine", "add", "--cycle", "api", "test_race", "--reason", "races")

    assert result.exit_code == 0, result.output
    assert "CI doesn't see" in result.output
    snippet = result.output.split("qasight.yaml:\n", 1)[1].split("\nor use", 1)[0]
    assert yaml.safe_load(snippet) == {
        "cycles": {"api": {"quarantine": [{"test": "test_race", "reason": "races"}]}}
    }


def test_no_ci_hint_for_a_shared_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from qasight.cli.commands.quarantine import ci_hint

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/testo")
    assert ci_hint(cycle="api", test="t", reason=None) is None
