"""GitHub issues for flaky tests (qasight/services/flaky_issues.py, github_issues.py)."""

from __future__ import annotations

import io
import json
import urllib.error
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import flaky_issues, flaky_service, github_issues
from qasight.services.flaky_issues import IssuesConfig
from qasight.services.github_issues import GitHubClient, GitHubError

CYCLE = "api"
KEY = "tests/test_a.py::test_x"


def _run(run_id: str, created_at: float, target_repo: str | None) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at + 1,
        test_kind="cycle",
        returncode=0,
        wall_duration_ms=1000.0,
        metrics_duration_ms=None,
        total_tests=None,
        passed=None,
        failed=None,
        broken=None,
        skipped=None,
        avg_case_ms=None,
        health_pct=None,
        target_repo=target_repo,
        snapshot_dir=f"static/history/{run_id}/artifacts",
        audit_json=None,
        cycle=CYCLE,
        repository="acme/shop",
    )


@pytest.fixture
def history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # noqa: ANN201
    """Set the cycle's per-run statuses of ``KEY``, oldest first."""

    def _set(statuses: list[str], *, key: str = KEY, message: str = "boom") -> None:
        runs = [_run(f"r{i}", float(i), str(tmp_path)) for i in range(len(statuses))]
        cases = {
            r.run_id: {
                key: {
                    "name": "test_x",
                    "full_name": key,
                    "group": "tests.test_a",
                    "status": s,
                    "message": message if s == "failed" else "",
                }
            }
            for r, s in zip(runs, statuses, strict=True)
        }
        newest_first = list(reversed(runs))
        monkeypatch.setattr(
            flaky_service,
            "_cycle_runs",
            lambda cycle, limit=None: list(newest_first)[:limit],
        )
        monkeypatch.setattr(flaky_service, "_cases_for_run", lambda run: cases[run.run_id])

    return _set


class FakeClient:
    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = existing or []
        self.calls: list[tuple[str, Any]] = []
        self.reject_assignees = False
        self.fail_list: Exception | None = None
        self.created = 0

    def list_open_issues(self, *, labels: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append(("list", labels))
        if self.fail_list:
            raise self.fail_list
        return list(self.existing)

    def create_issue(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create", kwargs))
        if self.reject_assignees and kwargs.get("assignees"):
            raise GitHubError("HTTP 422", status=422)
        number = 100 + self.created
        self.created += 1
        issue = {"number": number, "title": kwargs["title"], "body": kwargs["body"]}
        self.existing.append(issue)
        return {**issue, "html_url": f"https://github.com/acme/shop/issues/{number}"}

    def comment(self, number: int, body: str) -> None:
        self.calls.append(("comment", (number, body)))

    def close(self, number: int) -> None:
        self.calls.append(("close", number))

    def writes(self) -> list[tuple[str, Any]]:
        return [c for c in self.calls if c[0] != "list"]


def _sync(client: FakeClient, **kwargs: Any) -> flaky_issues.SyncResult:
    config = kwargs.pop("config", IssuesConfig(token="t", repository="acme/shop"))
    return flaky_issues.sync_cycle(
        cycle=CYCLE, client=client, config=config, repository="acme/shop", **kwargs
    )


def test_opens_one_issue_for_a_test_that_turned_flaky(history) -> None:  # noqa: ANN001
    history(["passed", "failed", "passed"])
    client = FakeClient()
    result = _sync(client)
    creates = [c for c in client.calls if c[0] == "create"]
    assert len(creates) == 1
    issue = creates[0][1]
    assert issue["title"] == "Flaky test: test_x (api)"
    assert issue["labels"] == ("flaky-test",)
    assert issue["assignees"] == ()
    assert flaky_issues.parse_marker(issue["body"]) == (CYCLE, KEY)
    assert "2 passed, 1 failed, 2 flips" in issue["body"]
    assert "```\nboom\n```" in issue["body"]
    assert "passed its last 10 runs" in issue["body"]
    assert [a.kind for a in result.actions] == ["open"]

    # The next pass finds the marker: no second issue, even when every flaky test is a candidate.
    again = _sync(client, only_new=False)
    assert [a.kind for a in again.actions] == ["exists"]
    assert len([c for c in client.calls if c[0] == "create"]) == 1


def test_no_duplicate_when_an_open_issue_has_the_marker_or_the_title(history) -> None:  # noqa: ANN001
    history(["passed", "failed", "passed"])
    marked = FakeClient(
        [{"number": 7, "title": "renamed", "body": flaky_issues.marker(CYCLE, KEY)}]
    )
    assert [a.number for a in _sync(marked).actions if a.kind == "exists"] == [7]
    assert marked.writes() == []

    # Issues the GitHub Action opened carry no marker, only the same title.
    titled = FakeClient([{"number": 8, "title": "Flaky test: test_x (api)", "body": "old"}])
    _sync(titled)
    assert titled.writes() == []

    # Another cycle's marker for the same test doesn't count.
    other = FakeClient([{"number": 9, "title": "x", "body": flaky_issues.marker("web", KEY)}])
    _sync(other)
    assert [c[0] for c in other.writes()] == ["create"]


def test_closes_after_n_green_runs_and_only_marked_issues(history) -> None:  # noqa: ANN001
    history(["passed", "failed", "passed", "failed", "passed", "passed", "passed"])
    issues = [
        {"number": 5, "title": "Flaky test: test_x (api)", "body": flaky_issues.marker(CYCLE, KEY)},
        {"number": 6, "title": "Flaky test: test_x (api)", "body": "no marker"},
    ]
    config = IssuesConfig(token="t", repository="acme/shop", close_after=3)

    # Within the default 20-run window it is still flaky: nothing closes.
    still = FakeClient(list(issues))
    _sync(still, config=config)
    assert still.writes() == []

    # Window 3: not flaky, and its last 3 runs passed.
    client = FakeClient(list(issues))
    result = _sync(client, config=config, window=3)
    assert [c[0] for c in client.writes()] == ["comment", "close"]
    assert client.writes()[1] == ("close", 5)
    assert "passed its last 3 runs" in client.writes()[0][1][1]
    assert [(a.kind, a.number) for a in result.actions] == [("close", 5)]

    # Needing 4 green runs, the 4th-last failure keeps it open.
    strict = FakeClient(list(issues))
    _sync(strict, config=replace(config, close_after=4), window=3)
    assert strict.writes() == []


def test_assigns_codeowners_users_and_skips_teams(history, tmp_path: Path) -> None:  # noqa: ANN001
    (tmp_path / "CODEOWNERS").write_text("tests/ @alice @acme/qa bob@example.com\n")
    history(["passed", "failed", "passed"])
    config = IssuesConfig(token="t", repository="acme/shop", assign=True)
    client = FakeClient()
    _sync(client, config=config, checkout=tmp_path)
    issue = client.writes()[0][1]
    assert issue["assignees"] == ("alice",)
    assert "Owners (CODEOWNERS): @alice @acme/qa bob@example.com" in issue["body"]

    # GitHub rejects an assignee that isn't a collaborator: open it unassigned.
    rejecting = FakeClient()
    rejecting.reject_assignees = True
    result = _sync(rejecting, config=config, checkout=tmp_path)
    creates = [c[1] for c in rejecting.calls if c[0] == "create"]
    assert [c.get("assignees", ()) for c in creates] == [("alice",), ()]
    assert [a.kind for a in result.actions] == ["open"] and not result.warnings


def test_dry_run_makes_no_write_calls(history) -> None:  # noqa: ANN001
    history(["passed", "failed", "passed"])
    client = FakeClient()
    result = _sync(client, dry_run=True)
    assert client.writes() == []
    assert flaky_issues.describe(result) == ["would open: Flaky test: test_x (api)"]


def test_opens_at_most_five_per_run(monkeypatch: pytest.MonkeyPatch, history) -> None:  # noqa: ANN001
    history(["passed", "failed", "passed"])
    tests = tuple(
        flaky_service.FlakyTest(
            key=f"k{i}", name=f"t{i}", group="g", runs=3, passed=2, failed=1, flips=2,
            flip_rate=1.0, last_status="passed", history=("passed", "failed", "passed"),
        )
        for i in range(7)
    )  # fmt: skip
    monkeypatch.setattr(flaky_service, "newly_flaky_tests", lambda **_: tests)
    client = FakeClient()
    result = _sync(client)
    assert len(client.writes()) == flaky_issues.MAX_OPEN_PER_RUN
    assert [a.kind for a in result.actions].count("deferred") == 2


def test_errors_never_raise(history, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    history(["passed", "failed", "passed"])
    client = FakeClient()
    client.fail_list = GitHubError("GitHub answered HTTP 403 to GET /issues", status=403)
    result = _sync(client)
    assert result.warnings and client.writes() == []

    env = {"TESTO_ISSUES": "github", "TESTO_GITHUB_TOKEN": "t"}
    broken = FakeClient()
    broken.fail_list = RuntimeError("unexpected")
    assert flaky_issues.sync_after_run(cycle=CYCLE, run_id="r2", env=env, client=broken) is None
    assert "qasight issues: skipped (RuntimeError: unexpected)" in capsys.readouterr().err


def test_run_hook_is_off_unless_enabled(history, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    history(["passed", "failed", "passed"])
    client = FakeClient()
    assert flaky_issues.sync_after_run(cycle=CYCLE, run_id="r2", env={}, client=client) is None
    assert client.calls == []

    env = {"TESTO_ISSUES": "github", "GITHUB_TOKEN": "t"}
    result = flaky_issues.sync_after_run(cycle=CYCLE, run_id="r2", env=env, client=client)
    assert result is not None and [a.kind for a in result.actions] == ["open"]
    assert "qasight issues: opened #100: Flaky test: test_x (api)" in capsys.readouterr().err
    # Run r1 (passed, failed) didn't make it flaky: nothing to open for it.
    quiet = flaky_issues.sync_after_run(cycle=CYCLE, run_id="r1", env=env, client=FakeClient())
    assert quiet is not None and quiet.actions == []


def test_config_from_env() -> None:
    config = flaky_issues.config_from_env(
        {
            "GITHUB_TOKEN": "g",
            "TESTO_GITHUB_TOKEN": "t",
            "TESTO_ISSUES_LABELS": "flaky, ci ,",
            "TESTO_ISSUES_ASSIGN": "1",
            "TESTO_ISSUES_CLOSE_AFTER": "4",
            "TESTO_ISSUES_REPO": "acme/other",
            "GITHUB_API_URL": "https://ghe.example/api/v3",
            "TESTO_UI_URL": "https://testo.example/",
        }
    )
    assert config == IssuesConfig(
        token="t",
        repository="acme/other",
        labels=("flaky", "ci"),
        assign=True,
        close_after=4,
        api_url="https://ghe.example/api/v3",
        ui_url="https://testo.example",
    )
    defaults = flaky_issues.config_from_env({"TESTO_ISSUES_CLOSE_AFTER": "x"})
    assert (defaults.labels, defaults.close_after, defaults.assign) == (("flaky-test",), 10, False)
    assert flaky_issues.enabled({"TESTO_ISSUES": "GitHub"})
    assert not flaky_issues.enabled({"TESTO_ISSUES": "jira"})


def test_marker_and_body_survive_hostile_names() -> None:
    key = "t.py::test_[a-->b`c] :x"
    mark = flaky_issues.marker("my cycle", key)
    assert "-->" not in mark[4:-3] and " " not in mark[5:-4]
    assert flaky_issues.parse_marker(f"text\n{mark}\nmore") == ("my cycle", key)
    test = flaky_service.FlakyTest(
        key=key, name="test_`x`", group="g", runs=3, passed=2, failed=1, flips=2,
        flip_rate=1.0, last_status="passed", history=("passed", "failed", "passed"),
    )  # fmt: skip
    body = flaky_issues.issue_body(
        test,
        cycle="api",
        key=key,
        message="```\n@someone <img>",
        page_url=flaky_issues.test_page_url("https://testo.example", "api", key),
    )
    assert "`` test_`x` ``" in body
    assert "````\n```\n@someone <img>\n````" in body
    assert (
        "https://testo.example/cycles/api/test?key=t.py%3A%3Atest_%5Ba--%3Eb%60c%5D%20%3Ax" in body
    )


class _Response(io.BytesIO):
    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_github_client_requests_and_errors() -> None:
    seen: list[Any] = []

    def opener(req: Any, timeout: float) -> _Response:
        seen.append((req, timeout))
        if req.get_method() == "GET":
            return _Response(
                json.dumps([{"number": 1}, {"number": 2, "pull_request": {}}]).encode()
            )
        return _Response(b'{"number": 3}')

    client = GitHubClient(
        token="secret", repository="acme/shop", api_url="https://ghe.example/api/v3/", opener=opener
    )
    assert client.list_open_issues(labels=("flaky-test",)) == [{"number": 1}]
    assert client.create_issue(title="t", body="b", labels=("flaky-test",)) == {"number": 3}
    client.close(3)
    get, create, close = (r for r, _ in seen)
    assert get.full_url == (
        "https://ghe.example/api/v3/repos/acme/shop/issues"
        "?state=open&per_page=100&labels=flaky-test&page=1"
    )
    assert get.get_header("Accept") == "application/vnd.github+json"
    assert get.get_header("Authorization") == "Bearer secret"
    assert get.get_header("User-agent")
    assert json.loads(create.data) == {"title": "t", "body": "b", "labels": ["flaky-test"]}
    assert close.get_method() == "PATCH"
    assert json.loads(close.data) == {"state": "closed", "state_reason": "completed"}
    assert all(timeout == 10.0 for _, timeout in seen)

    def http_error(req: Any, timeout: float) -> _Response:
        raise urllib.error.HTTPError(
            req.full_url, 422, "Unprocessable", {}, io.BytesIO(b'{"message": "Validation Failed"}')
        )

    with pytest.raises(GitHubError) as exc:
        GitHubClient(token="secret", repository="a/b", opener=http_error).comment(1, "x")
    assert exc.value.status == 422
    assert "Validation Failed" in str(exc.value) and "secret" not in str(exc.value)

    def down(req: Any, timeout: float) -> _Response:
        raise urllib.error.URLError("no route")

    with pytest.raises(GitHubError, match="unreachable"):
        GitHubClient(token="", repository="a/b", opener=down).list_open_issues()


def test_cli_sync_dry_run(monkeypatch: pytest.MonkeyPatch, history) -> None:  # noqa: ANN001
    history(["passed", "failed", "passed"])
    client = FakeClient()
    monkeypatch.setattr(github_issues, "GitHubClient", lambda **_: client)
    for name in ("TESTO_GITHUB_TOKEN", "GITHUB_TOKEN", "TESTO_ISSUES_REPO"):
        monkeypatch.delenv(name, raising=False)
    result = CliRunner().invoke(app, ["issues", "sync", "--cycle", CYCLE, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "dry run: acme/shop, cycle api" in result.output
    assert "would open: Flaky test: test_x (api)" in result.output
    assert client.writes() == []

    # A real sync needs a token.
    result = CliRunner().invoke(app, ["issues", "sync", "--cycle", CYCLE])
    assert result.exit_code == 2

    monkeypatch.setenv("GITHUB_TOKEN", "t")
    result = CliRunner().invoke(app, ["issues", "sync", "--cycle", CYCLE, "--repo", "acme/x"])
    assert result.exit_code == 0, result.output
    assert "opened #100: Flaky test: test_x (api)" in result.output
