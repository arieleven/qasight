"""Delivery metrics (testo_core/services/delivery_metrics.py): time to fix, suite time, main red time."""

from __future__ import annotations

from typing import Any

import pytest

from testo_core.history.views import CompletedRunView
from testo_core.repository.models import RunStatus
from testo_core.services import delivery_metrics, flaky_service
from testo_core.services.delivery_metrics import DAY, build_delivery, default_branch, span

NOW = 200 * DAY
START = NOW - 7 * DAY


def _run(
    run_id: str,
    days_ago: float,
    *,
    red: bool = False,
    branch: str | None = "main",
    base: str | None = None,
    commit: str | None = None,
    ms: float = 60_000.0,
    cycle: str = "api",
    stages: list[dict[str, Any]] | None = None,
) -> CompletedRunView:
    at = NOW - days_ago * DAY
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=at,
        started_at=at,
        finished_at=at + ms / 1000,
        test_kind="cycle",
        returncode=1 if red else 0,
        wall_duration_ms=ms,
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
        branch=branch,
        base_branch=base,
        commit=commit or run_id,
        stage_health=stages or [],
    )


@pytest.fixture
def cases(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, dict[str, Any]]]:
    table: dict[str, dict[str, dict[str, Any]]] = {}
    monkeypatch.setattr(flaky_service, "_cases_for_run", lambda run: table.get(run.run_id, {}))
    return table


def _set(table: dict[str, Any], run_id: str, **statuses: str) -> None:
    table[run_id] = {k: {"name": k, "status": v} for k, v in statuses.items()}


def test_time_to_fix_open_failures_and_retries(cases: dict[str, Any]) -> None:
    runs = [
        _run("r1", 20, red=True),  # test_a starts failing 20 days ago
        _run("r2", 5),  # fixed 15 days later (inside the period)
        _run("r3", 3, red=True),  # test_b fails ...
        _run("r3b", 3, red=False, commit="r3"),  # ... and passes on the same commit: a retry
        _run("r4", 2, red=True),  # test_c starts failing, still open
        _run("r5", 0.5, red=True),
    ]
    _set(cases, "r1", test_a="failed", test_b="passed", test_c="passed")
    _set(cases, "r2", test_a="passed", test_b="passed", test_c="passed")
    _set(cases, "r3", test_a="passed", test_b="failed", test_c="passed")
    _set(cases, "r3b", test_a="passed", test_b="passed", test_c="passed")
    _set(cases, "r4", test_a="passed", test_b="passed", test_c="failed")
    _set(cases, "r5", test_a="passed", test_b="passed", test_c="failed")

    d = build_delivery(runs, start=START, end=NOW)
    assert d.default_branch == "main"
    assert d.fixes == 1
    assert d.median_fix_s == pytest.approx(15 * DAY)
    assert d.open_failures == 1
    (old,) = d.oldest_open
    assert (old.name, old.runs) == ("test_c", 2)
    assert old.age_s == pytest.approx(1.5 * DAY)
    assert [b.count for b in d.open_by_age] == [0, 1, 0, 0]


def test_flaky_tests_and_removed_tests_are_not_open(cases: dict[str, Any]) -> None:
    runs = [_run(f"r{i}", 6 - i, red=i % 2 == 0) for i in range(6)]
    for i in range(6):
        _set(cases, f"r{i}", test_flip="failed" if i % 2 == 0 else "passed")
    _set(cases, "r4", test_flip="failed", test_gone="failed")
    d = build_delivery(runs, start=START, end=NOW)
    assert d.fixes == 0 and d.open_failures == 0


def test_main_red_time_and_longest_spell(cases: dict[str, Any]) -> None:  # noqa: ARG001
    runs = [
        _run("a", 9, red=True),  # red before the period: carries into it
        _run("b", 6),  # green: 1 day red inside the period
        _run("c", 2, red=True),
        _run("d", 1),  # 1 more day red
        _run("pr", 1.5, red=True, branch="feature", base="main"),  # a PR run doesn't count
    ]
    d = build_delivery(runs, start=START, end=NOW)
    assert d.red_pct == pytest.approx(round(100 * 2 / 7, 1))
    assert d.longest_red_s == pytest.approx(3 * DAY)  # the spell that began 9 days ago
    # The period before (14 to 7 days ago) saw its first run 9 days ago: red from then on.
    assert d.prev_red_pct == pytest.approx(100.0)


def test_first_try_green_on_pull_requests(cases: dict[str, Any]) -> None:  # noqa: ARG001
    runs = [
        _run("m", 6),
        _run("p1", 5, red=True, branch="f1", base="main", commit="c1"),
        _run("p1b", 4.9, branch="f1", base="main", commit="c1"),  # rerun of c1: ignored
        _run("p2", 4, branch="f1", base="main", commit="c2"),
        _run("p3", 3, branch="f2", base="main", commit="c3"),
    ]
    d = build_delivery(runs, start=START, end=NOW)
    assert d.first_try_commits == 3
    assert d.first_try_green_pct == pytest.approx(66.7)


def test_suite_time_ci_minutes_and_grown_stage(cases: dict[str, Any]) -> None:  # noqa: ARG001
    slow = [{"name": "e2e", "duration_s": 300}, {"name": "unit", "duration_s": 10}]
    fast = [{"name": "e2e", "duration_s": 100}, {"name": "unit", "duration_s": 10}]
    runs = [
        _run("o1", 10, ms=120_000, stages=fast),
        _run("o2", 9, ms=120_000, stages=fast),
        _run("n1", 3, ms=300_000, stages=slow),
        _run("n2", 2, ms=360_000, stages=slow),
        _run("n3", 1, ms=600_000, stages=slow),
    ]
    d = build_delivery(runs, start=START, end=NOW)
    (row,) = d.cycles
    assert row.suite_median_ms == 360_000
    assert row.suite_p90_ms == 600_000
    assert row.prev_suite_median_ms == 120_000
    assert row.ci_minutes == pytest.approx(21.0) and d.ci_minutes == pytest.approx(21.0)
    assert row.prev_ci_minutes == pytest.approx(4.0)
    assert (row.grown_stage, row.grown_stage_before_ms, row.grown_stage_after_ms) == (
        "e2e",
        100_000,
        300_000,
    )


def test_no_branches_means_every_run_counts(cases: dict[str, Any]) -> None:  # noqa: ARG001
    runs = [_run("a", 3, red=True, branch=None), _run("b", 2, branch=None)]
    d = build_delivery(runs, start=START, end=NOW)
    assert d.default_branch is None
    assert d.red_pct == pytest.approx(round(100 / 3, 1))  # measured from the first run
    assert d.first_try_green_pct is None


def test_default_branch_choice() -> None:
    assert default_branch([_run("a", 1, branch="x", base="develop")]) == "develop"
    assert default_branch([_run("a", 1, branch="master")]) == "master"
    assert default_branch([_run("a", 1, branch="feature")]) is None


def test_scope_and_span() -> None:
    assert (
        build_delivery([_run("a", 1, cycle="ui")], start=START, end=NOW, cycles={"api"}).cycles
        == []
    )
    assert [span(s) for s in (None, 30, 5 * 3600, 2.5 * DAY, 12 * DAY)] == [
        "–",
        "1m",
        "5h",
        "2.5d",
        "12d",
    ]
    assert delivery_metrics.AGE_BUCKETS[-1][0] == "Over a month"


def test_dashboard_delivery_api(monkeypatch: pytest.MonkeyPatch, cases: dict[str, Any]) -> None:
    import time

    from fastapi.testclient import TestClient

    from testo_api.main import create_app

    now = time.time()
    shift = now - NOW
    runs = [_run("a", 3, red=True), _run("b", 1)]
    runs = [r.__class__(**{**vars(r), "created_at": r.created_at + shift}) for r in runs]
    _set(cases, "a", test_a="failed")
    _set(cases, "b", test_a="passed")
    monkeypatch.setattr(
        "testo_core.history.read_model.list_recent_runs",
        lambda *, limit: runs,  # noqa: ARG005
    )
    client = TestClient(create_app())
    body = client.get("/api/v1/dashboard/delivery?days=7").json()
    assert body["default_branch"] == "main"
    assert body["fixes"] == 1 and body["median_fix_s"] == pytest.approx(2 * DAY)
    assert client.get("/api/v1/dashboard/delivery?days=0").status_code == 422
