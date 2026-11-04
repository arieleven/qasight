"""The project under test's health score (qasight/services/project_health.py)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import project_health
from qasight.services.test_history import CycleTests, TestSummary
from tests._history_fakes import cycle_runs_from


def _run(run_id: str, cycle: str, created_at: float, *, failed: int = 0) -> CompletedRunView:
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
        total_tests=100,
        passed=100 - failed,
        failed=failed,
        broken=0,
        skipped=0,
        avg_case_ms=None,
        health_pct=None,
        target_repo=None,
        snapshot_dir=None,
        audit_json=None,
        cycle=cycle,
        stage_health=[{"name": "unit", "tier": "unit", "total_tests": 100}],
    )


def _test(key: str, *, flaky: bool = False, slowdown_ms: int | None = None) -> TestSummary:
    return TestSummary(
        key=key,
        name=key,
        full_name="",
        group="g",
        stage="s",
        runs=10,
        passed=8 if flaky else 10,
        failed=2 if flaky else 0,
        last_status="passed",
        flaky=flaky,
        last_duration_ms=None,
        median_duration_ms=None,
        slowdown_ms=slowdown_ms,
        slowdown_pct=25.0 if slowdown_ms else None,
    )


@pytest.fixture
def history(monkeypatch):  # noqa: ANN001, ANN201
    web = replace(
        _run("web-2", "web", 4, failed=5),
        skipped=0,
        stage_health=[
            {"name": "unit", "tier": "unit", "total_tests": 60, "quarantined": ["test_q"]},
            {"name": "e2e", "tier": "e2e", "total_tests": 40},
        ],
    )
    runs = [web, _run("api-2", "api", 3), _run("web-1", "web", 2), _run("api-1", "api", 1)]
    monkeypatch.setattr(project_health, "list_cycle_runs", cycle_runs_from(runs))
    monkeypatch.setattr(
        project_health, "latest_run_per_cycle", lambda: {"web": runs[0], "api": runs[1]}
    )
    tests = {
        "web": [_test("w-flaky", flaky=True), _test("w-slow", slowdown_ms=40)]
        + [_test(f"w{i}") for i in range(98)],
        "api": [_test(f"a{i}") for i in range(100)],
    }
    monkeypatch.setattr(
        project_health.test_history,
        "list_cycle_tests",
        lambda cycle: CycleTests(cycle=cycle, runs_considered=2, tests=tuple(tests[cycle])),
    )
    return runs


def test_signals_score_the_whole_project(history) -> None:  # noqa: ANN001
    health = project_health.project_health()
    signals = {s.key: s for s in health.signals}
    assert signals["passing"].score == round(100 * 195 / 200)
    assert signals["passing"].value == "195 of 200 tests passed"
    assert signals["green_builds"].value == "3 of 4 runs passed"
    # 1 flaky of 200 tests = 0.5%, which costs 5 points.
    assert signals["stable"].score == 95
    assert signals["speed"].score == round(100 - 500 / 200)
    assert signals["hygiene"].value == "0 skipped, 1 quarantined of 200 tests"
    # 160 unit + 40 e2e, no integration: not a clean pyramid.
    assert signals["pyramid"].score == 70
    assert health.grade == project_health.grade_for(health.score)
    assert (health.cycles, health.runs_considered, health.tests) == (2, 4, 200)


def test_by_cycle_and_findings(history) -> None:  # noqa: ANN001
    health = project_health.project_health()
    web, api = health.by_cycle
    assert (web.cycle, web.passed_latest, web.failing, web.flaky, web.slower) == (
        "web",
        False,
        5,
        1,
        1,
    )
    assert (api.green_runs, api.runs) == (2, 2)
    assert api.score is not None and web.score is not None and api.score > web.score
    kinds = [f.kind for f in health.findings]
    assert kinds == ["red_cycle", "flaky", "slower", "quarantined"]
    assert health.findings[1].test_key == "w-flaky"
    assert health.findings[0].detail == "5 tests failing in the latest run."
    assert health.findings[2].detail.startswith("Got slower by 40 ms")


def test_slowdowns_read_like_the_ui() -> None:
    assert project_health._duration(850) == "850 ms"
    assert project_health._duration(13929) == "13.9 s"


def test_one_cycle_and_no_data(history) -> None:  # noqa: ANN001
    assert project_health.project_health(cycle="api").cycles == 1
    empty = project_health.project_health(cycle="nope")
    assert (empty.score, empty.grade, empty.cycles) == (None, None, 0)
    assert all(s.score is None for s in empty.signals)


@pytest.mark.parametrize(("score", "grade"), [(95, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F")])
def test_grades(score: int, grade: str) -> None:
    assert project_health.grade_for(score) == grade
