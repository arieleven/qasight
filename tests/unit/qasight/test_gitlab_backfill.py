"""``qasight ingest --from-gitlab``: GitLab pipelines' test reports recorded as history."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from defusedxml import ElementTree
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.services import gitlab_backfill

_PROJECT = "/api/v4/projects/shop%2Fmono"


def _case(name: str, status: str = "success", **extra: object) -> dict[str, object]:
    return {"name": name, "classname": "login", "status": status, "execution_time": 0.5, **extra}


def _pipeline(pid: int, day: int, status: str = "success") -> dict[str, object]:
    return {
        "id": pid,
        "status": status,
        "ref": "main",
        "sha": f"sha{pid}",
        "created_at": f"2026-11-{day:02d}T08:00:00.000Z",
        "updated_at": f"2026-11-{day:02d}T08:10:00.000Z",
        "web_url": f"https://gitlab.example.local/shop/mono/-/pipelines/{pid}",
    }


class _FakeGitLab(BaseHTTPRequestHandler):
    pipelines: list[dict[str, object]] = []
    reports: dict[str, dict[str, object]] = {}
    bridges: dict[str, list[dict[str, object]]] = {}
    calls: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 — http.server's name
        url = urlparse(self.path)
        type(self).calls.append(self.path)
        if self.headers.get("PRIVATE-TOKEN") != "glpat-test":
            return self._send(401, {"message": "401 Unauthorized"})
        if url.path == f"{_PROJECT}/pipelines":
            query = parse_qs(url.query)
            rows = [p for p in self.pipelines if p["ref"] in query.get("ref", [p["ref"]])]
            return self._send(200, sorted(rows, key=lambda p: -int(p["id"])))  # type: ignore[call-overload]
        parts = url.path.removeprefix(f"{_PROJECT}/pipelines/").split("/")
        if len(parts) == 2 and parts[1] == "test_report":
            return self._send(
                200, self.reports.get(parts[0], {"total_count": 0, "test_suites": []})
            )
        if len(parts) == 2 and parts[1] == "bridges":
            return self._send(200, self.bridges.get(parts[0], []))
        return self._send(404, {"message": "404 Not Found"})

    def _send(self, code: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def gitlab(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    _FakeGitLab.calls = []
    _FakeGitLab.pipelines = [
        _pipeline(101, 1),
        _pipeline(102, 2, "failed"),
        _pipeline(103, 3, "running"),  # not finished: left for the next pass
        _pipeline(104, 4),  # no test report
    ]
    _FakeGitLab.reports = {
        "101": {
            "test_suites": [
                {"name": "pytest pay", "total_time": 1, "test_cases": [_case("a"), _case("b")]},
                {"name": "behave web", "test_cases": [_case("Scenario: works")]},
            ]
        },
        "102": {
            "test_suites": [
                {
                    "name": "pytest pay",
                    "test_cases": [
                        _case("a"),
                        _case("b", "failed", stack_trace="AssertionError: 1 != 2\n  at x"),
                        _case("c", "skipped"),
                    ],
                }
            ]
        },
        "202": {"test_suites": [{"name": "behavex flow", "test_cases": [_case("Login")]}]},
    }
    _FakeGitLab.bridges = {
        "102": [
            {"pipeline": {"project_id": 7}, "downstream_pipeline": {"id": 202, "project_id": 7}}
        ]
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGitLab)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("TESTO_GITLAB_TOKEN", "glpat-test")
    for key in ("GITLAB_CI", "CI_PIPELINE_ID", "CI_SERVER_URL", "CI_PROJECT_ID", "GITHUB_ACTIONS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def _history(cycle: str) -> list[object]:
    from qasight.repository.db import get_repository

    return sorted(get_repository().list_runs(cycle=cycle, limit=50), key=lambda r: r.start_time)


def _ingest(url: str, *extra: str) -> object:
    args = ["ingest", "--from-gitlab", "--gitlab-url", url, "--project", "shop/mono", *extra]
    return CliRunner().invoke(app, args)


def test_each_job_becomes_a_cycle_dated_when_the_pipeline_ran(gitlab: str) -> None:
    result = _ingest(gitlab)
    assert result.exit_code == 0, result.output
    assert "Recorded 4 run(s) from GitLab pipelines into 3 cycle(s)" in result.output
    assert "1 pipeline(s) without test reports" in result.output

    pay = _history("pytest-pay")
    assert [(r.metadata_ or {}).get("ci_pipeline_id") for r in pay] == ["101", "102"]
    assert pay[0].start_time.replace(tzinfo=None).isoformat() == "2026-11-01T08:00:00"
    meta = pay[1].metadata_ or {}
    assert meta.get("backfilled_from") == "gitlab"
    assert (meta.get("ci_provider"), meta.get("ci_commit_sha")) == ("gitlab", "sha102")
    assert meta.get("ci_ref_name") == "main"
    assert pay[1].status != pay[0].status  # the failed test fails the run
    assert len(_history("behave-web")) == 1
    assert len(_history("behavex-flow")) == 1  # from the child pipeline
    assert "CI_PIPELINE_ID" not in os.environ  # the pipeline's facts don't leak out

    again = _ingest(gitlab)
    assert again.exit_code == 0, again.output
    assert "Recorded 0 run(s)" in again.output and "4 already recorded" in again.output
    assert len(_history("pytest-pay")) == 2


def test_one_cycle_for_the_whole_pipeline_and_ci_json(gitlab: str) -> None:
    result = _ingest(gitlab, "--cycle", "mono", "--ci", "--runs", "2")
    assert result.exit_code == 0, result.output
    event = json.loads(result.output.strip().splitlines()[-1])
    assert event == {
        "event": "gitlab_backfill_finished",
        "recorded": 1,
        "already": 0,
        "no_results": 1,
        "failed": 0,
        "cycles": ["mono"],
    }
    (run,) = _history("mono")
    assert (run.metadata_ or {}).get("ci_pipeline_id") == "102"  # the 2 newest finished: 102, 104


def test_branch_filter_and_bad_token_and_missing_names(
    gitlab: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _ingest(gitlab, "--branch", "release")
    assert result.exit_code == 0, result.output
    assert "Recorded 0 run(s)" in result.output
    assert any("ref=release" in call for call in _FakeGitLab.calls)

    monkeypatch.setenv("TESTO_GITLAB_TOKEN", "wrong")
    refused = _ingest(gitlab)
    assert refused.exit_code == 2
    assert "read_api" in refused.output

    unnamed = CliRunner().invoke(app, ["ingest", "--from-gitlab"])
    assert unnamed.exit_code == 2
    assert "--gitlab-url" in unnamed.output

    both = _ingest(gitlab, "--server", "https://qa.example.local")
    assert both.exit_code == 2 and "drop --server" in both.output


def test_a_suite_becomes_junit_qasight_reads() -> None:
    xml = gitlab_backfill.suite_junit_xml(
        {
            "name": 'pytest "pay"',
            "test_cases": [
                _case("ok", system_output="printed <b>"),
                _case("bad", "failed", stack_trace="boom & \x01bust\nline 2"),
                _case("err", "error", system_output="Traceback"),
                _case("skip", "skipped"),
            ],
        }
    )
    root = ElementTree.fromstring(xml.encode())
    assert root.attrib["name"] == 'pytest "pay"'
    assert (root.attrib["tests"], root.attrib["failures"], root.attrib["errors"]) == ("4", "1", "1")
    cases = {c.attrib["name"]: c for c in root.findall("testcase")}
    assert cases["bad"].find("failure").attrib["message"] == "boom & bust"
    assert cases["err"].find("error") is not None
    assert cases["skip"].find("skipped") is not None
    assert cases["ok"].find("system-out").text == "printed <b>"
    assert gitlab_backfill.cycle_for_suite("Pytest Pay 1/2") == "pytest-pay-1-2"
