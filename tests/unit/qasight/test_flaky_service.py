"""Flaky test detection over a cycle's run history (qasight/services/flaky_service.py)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from qasight import paths
from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import flaky_service
from tests._history_fakes import cycle_runs_from


def _run(run_id: str, *, cycle: str = "unit", created_at: float = 1.0) -> CompletedRunView:
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
        target_repo=None,
        snapshot_dir=f"static/history/{run_id}/artifacts",
        audit_json=None,
        cycle=cycle,
    )


def _write_results(root: Path, run: CompletedRunView, statuses: dict[str, str]) -> None:
    folder = root / str(run.snapshot_dir) / "unit" / "allure-results" / "pytest"
    folder.mkdir(parents=True, exist_ok=True)
    for name, status in statuses.items():
        (folder / f"{name}-result.json").write_text(
            json.dumps(
                {"historyId": name, "name": name, "fullName": f"tests.m.{name}", "status": status}
            ),
            encoding="utf-8",
        )


@pytest.mark.parametrize(
    ("statuses", "flaky"),
    [
        (["passed", "failed", "passed"], True),
        (["passed", "broken", "passed", "failed"], True),
        (["passed", "passed", "failed"], False),  # one flip: a regression, not flakiness
        (["failed", "failed", "failed"], False),
        (["passed", "skipped", "passed"], False),  # skipped runs don't count
        (["passed", "skipped", "failed", "skipped", "passed"], True),
    ],
)
def test_classify(statuses: list[str], flaky: bool) -> None:
    result = flaky_service.classify("k", [{"status": s, "name": "t"} for s in statuses])
    assert (result is not None) is flaky


def test_classify_scores_flip_rate_and_keeps_history() -> None:
    result = flaky_service.classify(
        "k", [{"status": s, "name": "test_x", "group": "m"} for s in ["passed", "failed", "passed"]]
    )
    assert result is not None
    assert (result.passed, result.failed, result.flips, result.flip_rate) == (2, 1, 2, 1.0)
    assert result.history == ("passed", "failed", "passed")
    assert result.last_status == "passed"


def test_find_flaky_tests_reads_each_runs_snapshot(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    # list_recent_runs returns newest first; another cycle's run is ignored.
    runs = [
        _run("r3", created_at=3),
        _run("other", cycle="nightly", created_at=2.5),
        _run("r2", created_at=2),
        _run("r1", created_at=1),
    ]
    _write_results(
        tmp_path, runs[3], {"test_flaky": "passed", "test_ok": "passed", "test_broke": "passed"}
    )
    _write_results(
        tmp_path, runs[2], {"test_flaky": "failed", "test_ok": "passed", "test_broke": "passed"}
    )
    _write_results(
        tmp_path, runs[0], {"test_flaky": "passed", "test_ok": "passed", "test_broke": "failed"}
    )
    _write_results(tmp_path, runs[1], {"test_flaky": "failed"})

    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(flaky_service, "list_cycle_runs", cycle_runs_from(runs))

    report = flaky_service.find_flaky_tests(cycle="unit")

    assert (report.runs_analyzed, report.tests_analyzed) == (3, 3)
    assert [t.name for t in report.flaky] == ["test_flaky"]
    assert report.flaky[0].history == ("passed", "failed", "passed")


def test_find_flaky_tests_limits_to_the_window(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    runs = [_run(f"r{i}", created_at=i) for i in range(5, 0, -1)]  # newest first
    for run, status in zip(runs, ["passed", "passed", "failed", "passed", "failed"], strict=True):
        _write_results(tmp_path, run, {"test_x": status})
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(flaky_service, "list_cycle_runs", cycle_runs_from(runs))

    # Last 2 runs: passed, passed. Not flaky. All 5: failed, passed, failed, passed, passed.
    assert flaky_service.find_flaky_tests(cycle="unit", window=2).flaky == ()
    assert len(flaky_service.find_flaky_tests(cycle="unit", window=5).flaky) == 1


def test_runs_without_a_snapshot_are_skipped(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    runs = [replace(_run("r1"), snapshot_dir=None)]
    monkeypatch.setattr(flaky_service, "list_cycle_runs", cycle_runs_from(runs))
    report = flaky_service.find_flaky_tests(cycle="unit")
    assert (report.runs_analyzed, report.flaky) == (0, ())


def test_newly_flaky_tests_are_the_ones_this_run_made_flaky(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    runs = [_run(f"r{i}", created_at=i) for i in range(4, 0, -1)]  # newest first
    history = {  # oldest → newest
        "test_new": ["passed", "failed", "passed", "failed"],  # flaky since r3
        "test_old": ["failed", "passed", "failed", "failed"],  # flaky since r3
        "test_now": ["passed", "passed", "failed", "passed"],  # flaky from r4 on
    }
    for i, run in enumerate(reversed(runs)):
        _write_results(tmp_path, run, {name: statuses[i] for name, statuses in history.items()})
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(flaky_service, "list_cycle_runs", cycle_runs_from(runs))

    assert [t.name for t in flaky_service.newly_flaky_tests(cycle="unit")] == ["test_now"]
    # As of r3, test_new and test_old had just made their second flip.
    assert sorted(t.name for t in flaky_service.newly_flaky_tests(cycle="unit", run_id="r3")) == [
        "test_new",
        "test_old",
    ]
    assert flaky_service.newly_flaky_tests(cycle="unit", run_id="missing") == ()


def _obs(status: str, *, branch: str | None = None, commit: str | None = None, **extra: object):  # noqa: ANN202
    return {"status": status, "name": "t", "branch": branch, "commit": commit, **extra}


def test_flips_are_counted_per_branch() -> None:
    # A pull request broke the test and then fixed it, between passing runs on main.
    observations = [
        _obs("passed", branch="main"),
        _obs("failed", branch="feature"),
        _obs("passed", branch="main"),
        _obs("passed", branch="feature"),
    ]
    assert flaky_service.classify("k", observations) is None


def test_flips_on_one_branch_are_flaky_and_name_the_branch() -> None:
    observations = [_obs(s, branch="main") for s in ["passed", "failed", "passed"]]
    result = flaky_service.classify("k", observations)
    assert result is not None
    assert result.reasons == ("flipped 2 times on main",)


def test_pass_and_fail_on_the_same_commit_is_flaky() -> None:
    observations = [_obs("passed", commit="abc"), _obs("failed", commit="abc")]
    result = flaky_service.classify("k", observations)
    assert result is not None
    assert result.reasons == ("passed and failed on the same commit (1 commit)",)


def test_a_pass_on_retry_is_flaky_even_in_one_run() -> None:
    result = flaky_service.classify("k", [_obs("passed", retried=True)])
    assert result is not None
    assert (result.retried, result.reasons) == (1, ("passed on retry in 1 run",))


def test_failing_every_recent_run_is_broken_not_flaky() -> None:
    statuses = ["passed", "failed", "passed", "failed", "failed", "failed"]
    observations = [_obs(s) for s in statuses]
    assert flaky_service.classify("k", observations) is None
    broken = flaky_service.broken("k", observations)
    assert broken is not None and broken.failing_runs == 3


def test_broken_streak_follows_the_latest_branch() -> None:
    observations = [
        _obs("failed", branch="main"),
        _obs("failed", branch="main"),
        _obs("passed", branch="feature"),
        _obs("failed", branch="main"),
    ]
    assert flaky_service.failing_streak(observations) == 3


def test_report_reads_retries_and_lists_broken_tests(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    runs = [_run("r3", created_at=3), _run("r2", created_at=2), _run("r1", created_at=1)]
    for run in runs:
        _write_results(tmp_path, run, {"test_broke": "failed"})
    folder = tmp_path / str(runs[0].snapshot_dir) / "unit" / "allure-results" / "pytest"
    (folder / "test_retry-result.json").write_text(
        json.dumps(
            {
                "historyId": "test_retry",
                "name": "test_retry",
                "status": "passed",
                "statusDetails": {"flaky": True, "message": "Passed on retry."},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(flaky_service, "list_cycle_runs", cycle_runs_from(runs))

    report = flaky_service.find_flaky_tests(cycle="unit")

    assert [t.name for t in report.flaky] == ["test_retry"]
    assert [(b.name, b.failing_runs) for b in report.broken] == [("test_broke", 3)]
