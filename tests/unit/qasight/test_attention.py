"""What needs someone now, across cycles (qasight/services/attention.py)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import attention
from qasight.services.flaky_service import FlakyTest
from qasight.services.run_failures import RunFailures
from qasight.services.test_history import CycleTests, TestSummary
from tests._history_fakes import cycle_runs_from


def _run(run_id: str, cycle: str, created_at: float, *, failed: bool) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at + 1,
        test_kind="cycle",
        returncode=1 if failed else 0,
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
        snapshot_dir=None,
        audit_json=None,
        cycle=cycle,
    )


def _summary(key: str, slowdown_ms: int | None) -> TestSummary:
    return TestSummary(
        key=key,
        name=key,
        full_name="",
        group="g",
        stage="s",
        runs=6,
        passed=6,
        failed=0,
        last_status="passed",
        flaky=False,
        last_duration_ms=None,
        median_duration_ms=None,
        slowdown_ms=slowdown_ms,
        slowdown_pct=50.0 if slowdown_ms else None,
    )


@pytest.fixture
def history(monkeypatch):  # noqa: ANN001, ANN201
    # Newest first: web failed its last three runs (the oldest of them on another branch).
    runs = [
        _run("web-4", "web", 4, failed=True),
        _run("api-2", "api", 3.5, failed=False),
        _run("web-3", "web", 3, failed=True),
        replace(_run("web-2b", "web", 2.5, failed=True), branch="feature"),
        _run("web-2", "web", 2, failed=True),
        _run("web-1", "web", 1, failed=False),
        _run("api-1", "api", 0.5, failed=True),
    ]
    runs[0] = replace(runs[0], branch="main")
    monkeypatch.setattr(attention, "list_cycle_runs", cycle_runs_from(runs))
    monkeypatch.setattr(attention, "latest_run_per_cycle", lambda: {"web": runs[0], "api": runs[1]})
    monkeypatch.setattr(
        attention,
        "load_run_failures",
        lambda run_id: RunFailures(run_id=run_id, runs_considered=4, tests=(), groups=()),
    )
    flaky = FlakyTest(
        key="k-flaky",
        name="test_flaky",
        group="g",
        runs=4,
        passed=2,
        failed=2,
        flips=3,
        flip_rate=1.0,
        last_status="failed",
        history=(),
    )
    monkeypatch.setattr(
        attention.flaky_service,
        "newly_flaky_tests",
        lambda *, cycle, run_id: (flaky,) if cycle == "web" else (),
    )
    slow = {
        "web": [_summary("w-slow", 30), _summary("w-same", None)],
        "api": [_summary("a-slow", 900)],
    }
    monkeypatch.setattr(
        attention.test_history,
        "list_cycle_tests",
        lambda cycle: CycleTests(cycle=cycle, runs_considered=6, tests=tuple(slow[cycle])),
    )
    return runs


def test_red_cycles_newly_flaky_and_slower_tests(history) -> None:  # noqa: ANN001
    result = attention.needs_attention()
    assert (result.cycles, result.passing_cycles) == (2, 1)
    [web] = result.failing
    assert (web.cycle, web.run_id, web.branch) == ("web", "web-4", "main")
    # web-4, web-3 and web-2 failed on main (or no branch); web-2b is another branch's run.
    assert web.failing_runs == 3
    assert [t.name for t in result.newly_flaky] == ["test_flaky"]
    # Only tests that got slower, most slowdown first, across cycles.
    assert [(t.cycle, t.key) for t in result.slower] == [("api", "a-slow"), ("web", "w-slow")]


def test_nothing_red(monkeypatch, history) -> None:  # noqa: ANN001
    monkeypatch.setattr(attention, "latest_run_per_cycle", lambda: {"api": history[1]})
    result = attention.needs_attention()
    assert (result.cycles, result.passing_cycles, result.failing) == (1, 1, ())
