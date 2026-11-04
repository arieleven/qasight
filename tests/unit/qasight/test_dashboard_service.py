from __future__ import annotations

import dataclasses

import pytest

from qasight.history.views import CompletedRunView, RunSessionView
from qasight.repository.models import RunStatus
from qasight.services.dashboard_service import DashboardService
from qasight.services.delta_service import DeltaComparisonService


def _completed(
    *,
    run_id: str,
    health_pct: float,
    failed: int,
    wall_duration_ms: float,
    passed: int = 10,
) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind="pytest",
        returncode=0,
        wall_duration_ms=wall_duration_ms,
        metrics_duration_ms=900,
        total_tests=10,
        passed=passed,
        failed=failed,
        broken=0,
        skipped=0,
        avg_case_ms=90.0,
        health_pct=health_pct,
        target_repo="/tmp/repo",
        snapshot_dir=None,
        audit_json=None,
    )


def _session(
    *,
    run_id: str,
    created_at: float,
    returncode: int,
    status: RunStatus,
    cycle: str | None = "smoke",
) -> RunSessionView:
    return RunSessionView(
        run_id=run_id,
        created_at=created_at,
        returncode=returncode,
        health_pct=99.0,
        total_tests=10,
        passed=10,
        failed=0,
        skipped=0,
        broken=0,
        status=status,
        links_under_static={
            "behavex": "/history/run-x/allure_reports/behavex/index.html",
        },
        cycle=cycle,
    )


def test_dashboard_overview_uses_existing_run_and_delta_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLURE_SERVER_URL", "http://allure.local")
    sessions = [
        _session(run_id="run-current", created_at=2.0, returncode=0, status=RunStatus.COMPLETED),
        _session(run_id="run-baseline", created_at=1.0, returncode=1, status=RunStatus.FAILED),
    ]
    runs = {
        "run-current": _completed(
            run_id="run-current", health_pct=98.0, failed=1, wall_duration_ms=900.0, passed=9
        ),
        "run-baseline": _completed(
            run_id="run-baseline", health_pct=93.0, failed=3, wall_duration_ms=1200.0, passed=7
        ),
    }
    service = DashboardService(
        run_sessions_loader=lambda limit: sessions[:limit],
        run_lookup=lambda run_id: runs.get(run_id),
        delta_service_factory=lambda: DeltaComparisonService(
            run_lookup=lambda run_id: runs.get(run_id)
        ),
    )

    overview = service.get_overview(recent_limit=2)

    assert overview.headline_kpis.latest_run_id == "run-current"
    assert overview.headline_kpis.cycle == "smoke"
    assert overview.headline_kpis.baseline_run_id == "run-baseline"
    assert overview.headline_kpis.health_pct == 98.0
    assert overview.trend_health.direction == "up"
    assert overview.trend_failed_count.direction == "down"
    assert overview.trend_duration.direction == "down"
    assert overview.reliability_rollup.status_summary.improvements >= 1
    assert overview.performance_rollup.status_summary.improvements >= 1
    assert overview.report_links.allure.state == "available"
    assert overview.report_links.allure.url is not None
    assert overview.report_links.behave.state == "available"
    assert len(overview.recent_runs) == 2
    assert (
        overview.recent_runs[0].compare_url
        == "/compare?current_run_id=run-current&baseline_run_id=run-baseline"
    )
    assert overview.data_freshness.degraded is False


def test_dashboard_overview_degrades_when_runs_missing() -> None:
    service = DashboardService(run_sessions_loader=lambda limit: [], run_lookup=lambda _: None)
    overview = service.get_overview()
    assert overview.headline_kpis.latest_run_id is None
    assert overview.trend_health.direction == "unknown"
    assert overview.reliability_rollup.status_summary.unknown == 0
    assert overview.report_links.allure.state in {"missing", "unknown"}
    assert overview.data_freshness.degraded is True
    assert "no_runs_available" in overview.data_freshness.notes


def test_dashboard_recent_runs_validates_limit() -> None:
    service = DashboardService(run_sessions_loader=lambda limit: [], run_lookup=lambda _: None)
    with pytest.raises(ValueError):
        service.get_recent_runs(limit=0)


def test_dashboard_baseline_and_compare_links_stay_within_the_same_cycle() -> None:
    sessions = [
        _session(run_id="a-2", created_at=3.0, returncode=0, status=RunStatus.COMPLETED, cycle="a"),
        _session(run_id="b-1", created_at=2.0, returncode=1, status=RunStatus.FAILED, cycle="b"),
        _session(run_id="a-1", created_at=1.0, returncode=0, status=RunStatus.COMPLETED, cycle="a"),
    ]
    runs = {
        "a-2": _completed(run_id="a-2", health_pct=100.0, failed=0, wall_duration_ms=1000.0),
        "b-1": _completed(run_id="b-1", health_pct=50.0, failed=5, wall_duration_ms=9000.0),
        "a-1": _completed(run_id="a-1", health_pct=100.0, failed=0, wall_duration_ms=1000.0),
    }
    service = DashboardService(
        run_sessions_loader=lambda limit: sessions[:limit],
        run_lookup=lambda run_id: runs.get(run_id),
        delta_service_factory=lambda: DeltaComparisonService(
            run_lookup=lambda run_id: runs.get(run_id)
        ),
    )

    overview = service.get_overview(recent_limit=3)

    assert overview.headline_kpis.cycle == "a"
    assert overview.headline_kpis.baseline_run_id == "a-1"
    # Against b-1 the health would read as "up"; against a-1 nothing changed.
    assert overview.trend_health.direction == "flat"
    assert overview.trend_duration.direction == "flat"
    expected_links = ["/compare?current_run_id=a-2&baseline_run_id=a-1", None, None]
    assert [r.compare_url for r in overview.recent_runs] == expected_links
    assert [r.compare_url for r in service.get_recent_runs(limit=2)] == expected_links[:2]


def test_dashboard_first_run_of_a_cycle_has_no_baseline_but_is_not_degraded() -> None:
    sessions = [
        _session(run_id="b-1", created_at=2.0, returncode=0, status=RunStatus.COMPLETED, cycle="b"),
        _session(run_id="a-1", created_at=1.0, returncode=0, status=RunStatus.COMPLETED, cycle="a"),
    ]
    run = _completed(run_id="b-1", health_pct=100.0, failed=0, wall_duration_ms=1000.0)
    service = DashboardService(
        run_sessions_loader=lambda limit: sessions[:limit],
        run_lookup=lambda run_id: run if run_id == "b-1" else None,
    )

    overview = service.get_overview()

    assert overview.headline_kpis.baseline_run_id is None
    assert overview.trend_health.direction == "unknown"
    assert overview.data_freshness.degraded is False
    assert overview.data_freshness.notes == ("no_previous_run_of_cycle",)


def test_dashboard_never_pairs_runs_without_a_recorded_cycle() -> None:
    sessions = [
        _session(run_id="x", created_at=2.0, returncode=0, status=RunStatus.COMPLETED, cycle=None),
        _session(run_id="y", created_at=1.0, returncode=0, status=RunStatus.COMPLETED, cycle=None),
    ]
    service = DashboardService(
        run_sessions_loader=lambda limit: sessions[:limit], run_lookup=lambda _: None
    )

    assert [r.compare_url for r in service.get_recent_runs(limit=2)] == [None, None]
    assert service.get_overview().headline_kpis.baseline_run_id is None


def test_dashboard_recent_runs_carry_cycle_name() -> None:
    session = _session(run_id="run-1", created_at=1.0, returncode=0, status=RunStatus.COMPLETED)
    sessions = [dataclasses.replace(session, cycle="fake-api")]
    service = DashboardService(
        run_sessions_loader=lambda limit: sessions, run_lookup=lambda _: None
    )
    assert service.get_recent_runs(limit=1)[0].cycle == "fake-api"


@pytest.mark.parametrize(
    ("current", "baseline", "direction"),
    [(6430.0, 6520.0, "flat"), (1300.0, 1000.0, "up"), (700.0, 1000.0, "down")],
)
def test_duration_trend_ignores_timing_noise(
    current: float, baseline: float, direction: str
) -> None:
    trend = DashboardService._build_trend(current=current, baseline=baseline, noise_floor_ms=100.0)
    assert trend.direction == direction


def test_count_trends_have_no_noise_floor() -> None:
    assert DashboardService._build_trend(current=1.0, baseline=0.0).direction == "up"
