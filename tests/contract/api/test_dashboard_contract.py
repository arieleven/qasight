from __future__ import annotations

from fastapi.testclient import TestClient

from qasight.services.dashboard_service import (
    DashboardDataFreshness,
    DashboardHeadlineKpis,
    DashboardOverview,
    DashboardRecentRun,
    DashboardReportLink,
    DashboardReportLinks,
    DashboardRollup,
    DashboardRollupSummary,
    DashboardTrendIndicator,
)
from qasight_api.main import create_app


def _overview_payload() -> DashboardOverview:
    return DashboardOverview(
        headline_kpis=DashboardHeadlineKpis(
            latest_run_id="run-1",
            latest_status="COMPLETED",
            health_pct=97.5,
            pass_count=39,
            fail_count=1,
            duration_ms=1250.0,
        ),
        trend_health=DashboardTrendIndicator(direction="up", delta_abs=2.5, delta_pct=2.63),
        trend_failed_count=DashboardTrendIndicator(
            direction="down", delta_abs=-1.0, delta_pct=-50.0
        ),
        trend_duration=DashboardTrendIndicator(
            direction="down", delta_abs=-150.0, delta_pct=-10.71
        ),
        reliability_rollup=DashboardRollup(
            status_summary=DashboardRollupSummary(
                regressions=1, improvements=3, unchanged=2, unknown=0
            ),
            top_highlights=("Failed tests improved by 1 tests.",),
        ),
        performance_rollup=DashboardRollup(
            status_summary=DashboardRollupSummary(
                regressions=0, improvements=2, unchanged=1, unknown=0
            ),
            top_highlights=("Wall duration improved by 150.00 ms.",),
        ),
        report_links=DashboardReportLinks(
            allure=DashboardReportLink(url="http://allure/reports/run-1", state="available"),
            behave=DashboardReportLink(
                url="/history/run-1/allure_reports/behavex/index.html", state="available"
            ),
        ),
        recent_runs=(
            DashboardRecentRun(
                run_id="run-1",
                cycle="smoke",
                created_at=1.0,
                status="COMPLETED",
                returncode=0,
                health_pct=97.5,
                duration_ms=1250.0,
                run_detail_url="/runs/run-1",
                compare_url="/compare?current_run_id=run-1&baseline_run_id=run-0",
            ),
        ),
        data_freshness=DashboardDataFreshness(
            generated_at=100.0,
            source_window_size=2,
            degraded=False,
            notes=(),
        ),
    )


def test_dashboard_overview_contract(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "qasight_api.routes.dashboard.DashboardService.get_overview",
        lambda self, recent_limit=5: _overview_payload(),  # noqa: ARG005
    )
    client = TestClient(create_app())
    resp = client.get("/api/v1/dashboard/overview")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload.keys()) == {
        "headline_kpis",
        "trend_indicators",
        "reliability_rollup",
        "performance_rollup",
        "report_links",
        "recent_runs",
        "data_freshness",
    }
    assert payload["headline_kpis"]["latest_run_id"] == "run-1"
    assert payload["trend_indicators"]["health"]["direction"] == "up"
    assert payload["report_links"]["allure"]["state"] == "available"
    assert payload["recent_runs"][0]["run_detail_url"] == "/runs/run-1"


def test_dashboard_recent_runs_contract(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "qasight_api.routes.dashboard.DashboardService.get_recent_runs",
        lambda self, limit=10: (  # noqa: ARG005
            DashboardRecentRun(
                run_id="run-9",
                cycle="smoke",
                created_at=9.0,
                status="FAILED",
                returncode=1,
                health_pct=None,
                duration_ms=None,
                run_detail_url="/runs/run-9",
                compare_url=None,
            ),
        ),
    )
    client = TestClient(create_app())
    resp = client.get("/api/v1/dashboard/runs/recent?limit=1")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload.keys()) == {"items", "generated_at"}
    assert payload["items"][0]["run_id"] == "run-9"
    assert payload["items"][0]["cycle"] == "smoke"


def test_dashboard_endpoint_validation_errors_follow_error_envelope(monkeypatch) -> None:  # noqa: ANN001
    def _raise_value(self, recent_limit=5):  # noqa: ARG001
        raise ValueError("recent_limit must be greater than zero.")

    monkeypatch.setattr("qasight_api.routes.dashboard.DashboardService.get_overview", _raise_value)
    client = TestClient(create_app())
    resp = client.get("/api/v1/dashboard/overview?recent_limit=0")
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error"]["code"] == "invalid_input"
    assert payload["request_id"]


def test_dashboard_attention_contract(monkeypatch) -> None:  # noqa: ANN001
    from qasight.services.attention import Attention, AttentionTest, FailingCycle

    monkeypatch.setattr(
        "qasight_api.routes.dashboard.needs_attention",
        lambda: Attention(
            cycles=2,
            passing_cycles=1,
            failing=(
                FailingCycle(
                    cycle="web",
                    run_id="run-9",
                    created_at=1.0,
                    branch="main",
                    failed_tests=4,
                    new_failures=1,
                    failing_runs=3,
                ),
            ),
            newly_flaky=(AttentionTest(cycle="web", key="k1", name="test_checkout"),),
            slower=(
                AttentionTest(
                    cycle="api", key="k2", name="test_search", slowdown_ms=1500, slowdown_pct=50.0
                ),
            ),
        ),
    )
    payload = TestClient(create_app()).get("/api/v1/dashboard/attention").json()
    assert (payload["cycles"], payload["passing_cycles"]) == (2, 1)
    assert payload["failing"][0] == {
        "cycle": "web",
        "run_id": "run-9",
        "created_at": 1.0,
        "branch": "main",
        "failed_tests": 4,
        "new_failures": 1,
        "failing_runs": 3,
    }
    assert payload["newly_flaky"][0]["slowdown_ms"] is None
    assert payload["slower"][0]["slowdown_pct"] == 50.0
