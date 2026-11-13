"""The weekly / daily digest (qasight/services/run_digest.py) and ``qasight notify --digest``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight import paths
from qasight.cli.app import app
from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import run_alerts, run_digest

DAY = 86400.0
NOW = 100 * DAY


def _run(run_id: str, *, at: float, returncode: int, passed: int, failed: int) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=at,
        started_at=at,
        finished_at=at + 1,
        test_kind="cycle",
        returncode=returncode,
        wall_duration_ms=1000.0,
        metrics_duration_ms=None,
        total_tests=passed + failed,
        passed=passed,
        failed=failed,
        broken=0,
        skipped=0,
        avg_case_ms=None,
        health_pct=None,
        target_repo=None,
        snapshot_dir=f"static/history/{run_id}/artifacts",
        audit_json=None,
        cycle="api",
    )


# Oldest first: (run id, days ago, {test: (status, ms)})
_HISTORY = [
    (
        "p1",
        13,
        {"test_login": ("failed", 100), "test_flip": ("passed", 10), "test_slow": ("passed", 100)},
    ),
    (
        "p2",
        10,
        {"test_login": ("failed", 100), "test_flip": ("passed", 10), "test_slow": ("passed", 100)},
    ),
    (
        "n1",
        6,
        {"test_login": ("passed", 100), "test_flip": ("failed", 10), "test_slow": ("passed", 900)},
    ),
    (
        "n2",
        4,
        {"test_login": ("passed", 100), "test_flip": ("passed", 10), "test_slow": ("passed", 900)},
    ),
    (
        "n3",
        1,
        {"test_login": ("passed", 100), "test_flip": ("failed", 10), "test_slow": ("passed", 900)},
    ),
]


@pytest.fixture
def history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[CompletedRunView]:
    runs: list[CompletedRunView] = []
    for run_id, ago, tests in _HISTORY:
        failed = sum(1 for s, _ in tests.values() if s == "failed")
        runs.append(
            _run(
                run_id,
                at=NOW - ago * DAY,
                returncode=1 if failed else 0,
                passed=len(tests) - failed,
                failed=failed,
            )
        )
        folder = tmp_path / f"static/history/{run_id}/artifacts" / "unit" / "allure-results"
        folder.mkdir(parents=True)
        for name, (status, ms) in tests.items():
            (folder / f"{name}-result.json").write_text(
                json.dumps(
                    {"historyId": name, "name": name, "status": status, "start": 0, "stop": ms}
                ),
                encoding="utf-8",
            )
    runs.reverse()  # newest first, like the repository
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(run_digest, "list_recent_runs", lambda *, limit: runs)
    return runs


def test_weekly_digest_compares_with_the_week_before(history: list[CompletedRunView]) -> None:
    digest = run_digest.build_digest(period="week", now=NOW)
    (d,) = digest.cycles
    assert (d.runs, d.green, d.prev_runs, d.prev_green) == (3, 1, 2, 0)
    assert d.newly_flaky == ("test_flip",)
    assert d.fixed == ("test_login",)
    assert d.still_failing == ("test_flip",)
    assert [s.name for s in d.slower] == ["test_slow"]
    assert d.slower[0].before_ms == 100 and d.slower[0].after_ms == 900
    assert run_digest.headline(d, "week").startswith(
        "3 runs, 1 green (33%, last week 2 runs, 0% green)"
    )


def test_a_cycle_with_no_runs_in_the_period_is_left_out(history: list[CompletedRunView]) -> None:
    assert run_digest.build_digest(period="day", now=NOW - 2 * DAY).cycles == ()
    assert run_digest.build_digest(period="week", cycles=["other"], now=NOW).cycles == ()


def test_payloads(history: list[CompletedRunView]) -> None:
    digest = run_digest.build_digest(period="week", now=NOW)
    slack = run_digest.build_payload("slack", digest, url="https://qasight.example")
    text = json.dumps(slack)
    assert "Weekly test digest" in slack["blocks"][0]["text"]["text"]
    assert "Turned flaky (1)" in text and "Fixed (1)" in text and "Got slower (1)" in text
    assert "https://qasight.example" in text
    teams = run_digest.build_payload("teams", digest)
    card = teams["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert any("Still failing" in b["text"] for b in card["body"])


def test_notify_digest_cli(
    history: list[CompletedRunView], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(run_alerts, "post", lambda url, payload: sent.append((url, payload)))
    monkeypatch.setattr(run_digest, "datetime", _FrozenDatetime)
    runner = CliRunner()

    result = runner.invoke(app, ["notify", "--to", "slack", "--digest", "week", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Weekly test digest" in result.output and not sent

    monkeypatch.setenv("TESTO_SLACK_WEBHOOK_URL", "https://hooks.example/x")
    result = runner.invoke(app, ["notify", "--to", "slack", "--digest", "week"])
    assert result.exit_code == 0, result.output
    assert len(sent) == 1 and "digest sent" in result.output

    result = runner.invoke(app, ["notify", "--to", "slack", "--digest", "month"])
    assert result.exit_code == 2


class _FrozenDatetime(run_digest.datetime):  # type: ignore[misc,name-defined]
    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def,override]
        return run_digest.datetime.fromtimestamp(NOW, tz=tz)


def test_a_pull_request_that_broke_and_fixed_a_test_is_not_flaky(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The digest judges flakiness per branch, like the flaky page (one rule everywhere)."""
    import dataclasses

    from qasight.services import flaky_service

    # Oldest first: main green, the pull request breaks then fixes it, main green again.
    plan = [("m1", "main", "passed"), ("f1", "feature", "failed"), ("f2", "feature", "passed")]
    plan.append(("m2", "main", "passed"))
    runs = [
        dataclasses.replace(
            _run(
                run_id,
                at=NOW - (5 - i) * DAY,
                returncode=int(status == "failed"),
                passed=int(status == "passed"),
                failed=int(status == "failed"),
            ),
            branch=branch,
        )
        for i, (run_id, branch, status) in enumerate(plan)
    ]
    cases = {run_id: {"t": {"name": "test_pr", "status": status}} for run_id, _, status in plan}
    monkeypatch.setattr(run_digest, "list_recent_runs", lambda *, limit: list(reversed(runs)))
    monkeypatch.setattr(flaky_service, "_cases_for_run", lambda run: cases[run.run_id])

    (d,) = run_digest.build_digest(period="week", now=NOW).cycles
    assert d.newly_flaky == ()
    # The same history on one branch flips twice, which is flaky.
    one_branch = [(dataclasses.replace(r, branch="main"), cases[r.run_id]) for r in runs]
    assert flaky_service.flaky_keys(one_branch) == {"t"}
