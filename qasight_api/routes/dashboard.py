from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query

from qasight.services.attention import needs_attention
from qasight.services.dashboard_service import DashboardRecentRun, DashboardService
from qasight_api.models import (
    AttentionFailingCycle,
    AttentionTestItem,
    DashboardAttentionResponse,
    DashboardDataFreshnessResponse,
    DashboardHeadlineKpis,
    DashboardOverviewResponse,
    DashboardRecentRunItem,
    DashboardRecentRunsResponse,
    DashboardReportLinkResponse,
    DashboardReportLinksResponse,
    DashboardRollupResponse,
    DashboardRollupSummaryResponse,
    DashboardTrendIndicator,
    DeliveryResponse,
)

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/dashboard/overview", response_model=DashboardOverviewResponse)
def get_dashboard_overview(recent_limit: int = 5) -> DashboardOverviewResponse:
    service = DashboardService()
    try:
        payload = service.get_overview(recent_limit=recent_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DashboardOverviewResponse(
        headline_kpis=DashboardHeadlineKpis(
            latest_run_id=payload.headline_kpis.latest_run_id,
            latest_status=payload.headline_kpis.latest_status,
            health_pct=payload.headline_kpis.health_pct,
            pass_count=payload.headline_kpis.pass_count,
            fail_count=payload.headline_kpis.fail_count,
            duration_ms=payload.headline_kpis.duration_ms,
            cycle=payload.headline_kpis.cycle,
            baseline_run_id=payload.headline_kpis.baseline_run_id,
        ),
        trend_indicators={
            "health": DashboardTrendIndicator(
                direction=payload.trend_health.direction,
                delta_abs=payload.trend_health.delta_abs,
                delta_pct=payload.trend_health.delta_pct,
            ),
            "failed_count": DashboardTrendIndicator(
                direction=payload.trend_failed_count.direction,
                delta_abs=payload.trend_failed_count.delta_abs,
                delta_pct=payload.trend_failed_count.delta_pct,
            ),
            "duration": DashboardTrendIndicator(
                direction=payload.trend_duration.direction,
                delta_abs=payload.trend_duration.delta_abs,
                delta_pct=payload.trend_duration.delta_pct,
            ),
        },
        reliability_rollup=DashboardRollupResponse(
            status_summary=DashboardRollupSummaryResponse(
                regressions=payload.reliability_rollup.status_summary.regressions,
                improvements=payload.reliability_rollup.status_summary.improvements,
                unchanged=payload.reliability_rollup.status_summary.unchanged,
                unknown=payload.reliability_rollup.status_summary.unknown,
            ),
            top_highlights=list(payload.reliability_rollup.top_highlights),
        ),
        performance_rollup=DashboardRollupResponse(
            status_summary=DashboardRollupSummaryResponse(
                regressions=payload.performance_rollup.status_summary.regressions,
                improvements=payload.performance_rollup.status_summary.improvements,
                unchanged=payload.performance_rollup.status_summary.unchanged,
                unknown=payload.performance_rollup.status_summary.unknown,
            ),
            top_highlights=list(payload.performance_rollup.top_highlights),
        ),
        report_links=DashboardReportLinksResponse(
            allure=DashboardReportLinkResponse(
                url=payload.report_links.allure.url,
                state=payload.report_links.allure.state,
            ),
            behave=DashboardReportLinkResponse(
                url=payload.report_links.behave.url,
                state=payload.report_links.behave.state,
            ),
        ),
        recent_runs=[_to_recent_run_item(item) for item in payload.recent_runs],
        data_freshness=DashboardDataFreshnessResponse(
            generated_at=payload.data_freshness.generated_at,
            source_window_size=payload.data_freshness.source_window_size,
            degraded=payload.data_freshness.degraded,
            notes=list(payload.data_freshness.notes),
        ),
    )


@router.get("/dashboard/attention", response_model=DashboardAttentionResponse)
def get_dashboard_attention() -> DashboardAttentionResponse:
    """Red cycles (most new failures first), newly flaky tests and tests that got slower."""
    attention = needs_attention()
    return DashboardAttentionResponse(
        cycles=attention.cycles,
        passing_cycles=attention.passing_cycles,
        failing=[AttentionFailingCycle(**vars(c)) for c in attention.failing],
        newly_flaky=[AttentionTestItem(**vars(t)) for t in attention.newly_flaky],
        slower=[AttentionTestItem(**vars(t)) for t in attention.slower],
    )


@router.get("/dashboard/delivery", response_model=DeliveryResponse)
def get_dashboard_delivery(days: int = Query(7, ge=1, le=90)) -> DeliveryResponse:
    """Time to fix, open failures by age, CI time and default-branch red time over the last ``days``."""
    from qasight.history.read_model import list_recent_runs
    from qasight.services.delivery_metrics import DAY, build_delivery

    end = time.time()
    delivery = build_delivery(list_recent_runs(limit=5000), start=end - days * DAY, end=end)
    return DeliveryResponse.model_validate(delivery.to_dict())


@router.get("/dashboard/runs/recent", response_model=DashboardRecentRunsResponse)
def get_dashboard_recent_runs(limit: int = 10) -> DashboardRecentRunsResponse:
    service = DashboardService()
    try:
        items = service.get_recent_runs(limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DashboardRecentRunsResponse(
        items=[_to_recent_run_item(item) for item in items], generated_at=time.time()
    )


def _to_recent_run_item(item: DashboardRecentRun) -> DashboardRecentRunItem:
    return DashboardRecentRunItem(
        run_id=item.run_id,
        cycle=item.cycle,
        created_at=item.created_at,
        status=item.status,
        returncode=item.returncode,
        health_pct=item.health_pct,
        duration_ms=item.duration_ms,
        run_detail_url=item.run_detail_url,
        compare_url=item.compare_url,
        branch=item.branch,
        commit=item.commit,
    )
