from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from qasight.history.read_model import get_run, list_run_sessions
from qasight.history.views import CompletedRunView, RunSessionView
from qasight.services.delta_models import DeltaComparisonResult, MetricDelta
from qasight.services.delta_service import (
    DeltaComparisonError,
    DeltaComparisonService,
    within_noise,
)

TrendDirection = Literal["up", "down", "flat", "unknown"]
LinkState = Literal["available", "missing", "unknown"]


@dataclass(frozen=True)
class DashboardTrendIndicator:
    direction: TrendDirection
    delta_abs: float | None
    delta_pct: float | None


@dataclass(frozen=True)
class DashboardRollupSummary:
    regressions: int
    improvements: int
    unchanged: int
    unknown: int


@dataclass(frozen=True)
class DashboardRollup:
    status_summary: DashboardRollupSummary
    top_highlights: tuple[str, ...]


@dataclass(frozen=True)
class DashboardReportLink:
    url: str | None
    state: LinkState


@dataclass(frozen=True)
class DashboardReportLinks:
    allure: DashboardReportLink
    behave: DashboardReportLink


@dataclass(frozen=True)
class DashboardRecentRun:
    run_id: str
    cycle: str | None
    created_at: float
    status: str | None
    returncode: int
    health_pct: float | None
    duration_ms: float | None
    run_detail_url: str
    compare_url: str | None
    branch: str | None = None
    commit: str | None = None


@dataclass(frozen=True)
class DashboardDataFreshness:
    generated_at: float
    source_window_size: int
    degraded: bool
    notes: tuple[str, ...]


@dataclass(frozen=True)
class DashboardHeadlineKpis:
    latest_run_id: str | None
    latest_status: str | None
    health_pct: float | None
    pass_count: int | None
    fail_count: int | None
    duration_ms: float | None
    # Trends compare the latest run with the previous run of this cycle.
    cycle: str | None = None
    baseline_run_id: str | None = None


@dataclass(frozen=True)
class DashboardOverview:
    headline_kpis: DashboardHeadlineKpis
    trend_health: DashboardTrendIndicator
    trend_failed_count: DashboardTrendIndicator
    trend_duration: DashboardTrendIndicator
    reliability_rollup: DashboardRollup
    performance_rollup: DashboardRollup
    report_links: DashboardReportLinks
    recent_runs: tuple[DashboardRecentRun, ...]
    data_freshness: DashboardDataFreshness


# How far back to look for an earlier run of the same cycle when other cycles ran in between.
_BASELINE_SEARCH_WINDOW = 20


def _previous_same_cycle(sessions: list[RunSessionView], index: int) -> RunSessionView | None:
    """Next older session of the same cycle as ``sessions[index]`` (newest-first list).

    Comparing runs of different cycles reports every test of the other cycle as
    added or removed, so baselines only ever come from the same cycle. Runs
    without a recorded cycle have no known peer.
    """
    cycle = sessions[index].cycle
    if cycle is None:
        return None
    return next((s for s in sessions[index + 1 :] if s.cycle == cycle), None)


class DashboardService:
    def __init__(
        self,
        *,
        run_sessions_loader: Callable[[int], list[RunSessionView]] | None = None,
        run_lookup: Callable[[str], CompletedRunView | None] | None = None,
        delta_service_factory: Callable[[], DeltaComparisonService] | None = None,
    ) -> None:
        self._run_sessions_loader = run_sessions_loader or (
            lambda limit: list_run_sessions(limit=limit)
        )
        self._run_lookup = run_lookup or (lambda run_id: get_run(run_id=run_id))
        self._delta_service_factory = delta_service_factory or DeltaComparisonService

    def get_recent_runs(self, *, limit: int = 10) -> tuple[DashboardRecentRun, ...]:
        if limit <= 0:
            raise ValueError("limit must be greater than zero.")
        sessions = self._run_sessions_loader(max(_BASELINE_SEARCH_WINDOW, limit))
        return tuple(
            self._build_recent_run(index=i, session=sessions[i], sessions=sessions)
            for i in range(min(len(sessions), limit))
        )

    def get_overview(self, *, recent_limit: int = 5) -> DashboardOverview:
        if recent_limit <= 0:
            raise ValueError("recent_limit must be greater than zero.")
        source_limit = max(_BASELINE_SEARCH_WINDOW, recent_limit)
        sessions = self._run_sessions_loader(source_limit)
        generated_at = time.time()

        notes: list[str] = []
        degraded = False

        latest_session = sessions[0] if sessions else None
        latest_run = self._run_lookup(latest_session.run_id) if latest_session else None
        if latest_session and latest_run is None:
            degraded = True
            notes.append("latest_run_details_missing")
        if not latest_session:
            degraded = True
            notes.append("no_runs_available")

        baseline_session = _previous_same_cycle(sessions, 0) if sessions else None
        baseline_run = None
        if baseline_session is not None:
            baseline_run = self._run_lookup(baseline_session.run_id)
            if baseline_run is None:
                degraded = True
                notes.append("baseline_run_details_missing")

        delta_result: DeltaComparisonResult | None = None
        if latest_run is not None and baseline_run is not None:
            try:
                delta_result = self._delta_service_factory().compare_runs(
                    current_run_id=latest_run.run_id,
                    baseline_run_id=baseline_run.run_id,
                )
            except DeltaComparisonError:
                degraded = True
                notes.append("delta_comparison_unavailable")
        elif latest_run is not None and baseline_session is None:
            # The first run of a cycle has nothing to trend against; that is not a data problem.
            notes.append("no_previous_run_of_cycle")

        report_links = self._build_report_links(latest_session=latest_session)

        return DashboardOverview(
            headline_kpis=self._build_headline(
                latest_run=latest_run,
                latest_session=latest_session,
                baseline_session=baseline_session,
            ),
            trend_health=self._build_trend(
                current=latest_run.health_pct if latest_run else None,
                baseline=baseline_run.health_pct if baseline_run else None,
            ),
            trend_failed_count=self._build_trend(
                current=float(latest_run.failed)
                if latest_run and latest_run.failed is not None
                else None,
                baseline=float(baseline_run.failed)
                if baseline_run and baseline_run.failed is not None
                else None,
            ),
            trend_duration=self._build_trend(
                current=latest_run.wall_duration_ms if latest_run else None,
                baseline=baseline_run.wall_duration_ms if baseline_run else None,
                noise_floor_ms=100.0,
            ),
            reliability_rollup=self._build_rollup(delta_result=delta_result, group="reliability"),
            performance_rollup=self._build_rollup(delta_result=delta_result, group="performance"),
            report_links=report_links,
            recent_runs=tuple(
                self._build_recent_run(index=i, session=sessions[i], sessions=sessions)
                for i in range(min(len(sessions), recent_limit))
            ),
            data_freshness=DashboardDataFreshness(
                generated_at=generated_at,
                source_window_size=len(sessions),
                degraded=degraded,
                notes=tuple(notes),
            ),
        )

    def _build_headline(
        self,
        *,
        latest_run: CompletedRunView | None,
        latest_session: RunSessionView | None,
        baseline_session: RunSessionView | None,
    ) -> DashboardHeadlineKpis:
        return DashboardHeadlineKpis(
            latest_run_id=latest_run.run_id
            if latest_run
            else (latest_session.run_id if latest_session else None),
            latest_status=latest_run.status.value
            if latest_run and latest_run.status is not None
            else None,
            health_pct=latest_run.health_pct
            if latest_run
            else (latest_session.health_pct if latest_session else None),
            pass_count=latest_run.passed
            if latest_run
            else (latest_session.passed if latest_session else None),
            fail_count=latest_run.failed
            if latest_run
            else (latest_session.failed if latest_session else None),
            duration_ms=latest_run.wall_duration_ms if latest_run else None,
            cycle=latest_session.cycle if latest_session else None,
            baseline_run_id=baseline_session.run_id if baseline_session else None,
        )

    def _build_rollup(
        self,
        *,
        delta_result: DeltaComparisonResult | None,
        group: Literal["reliability", "performance"],
    ) -> DashboardRollup:
        if delta_result is None:
            return DashboardRollup(
                status_summary=DashboardRollupSummary(
                    regressions=0, improvements=0, unchanged=0, unknown=0
                ),
                top_highlights=(),
            )

        metrics = [metric for metric in delta_result.metrics if metric.group == group]
        summary = DashboardRollupSummary(
            regressions=sum(1 for metric in metrics if metric.classification == "regression"),
            improvements=sum(1 for metric in metrics if metric.classification == "improvement"),
            unchanged=sum(1 for metric in metrics if metric.classification == "neutral"),
            unknown=sum(1 for metric in metrics if metric.classification == "unknown"),
        )
        highlights = tuple(self._highlights_for_group(metrics=metrics, delta_result=delta_result))
        return DashboardRollup(status_summary=summary, top_highlights=highlights)

    def _highlights_for_group(
        self,
        *,
        metrics: list[MetricDelta],
        delta_result: DeltaComparisonResult,
    ) -> list[str]:
        keys = {metric.metric_key for metric in metrics}
        return [
            line for line in delta_result.highlights if any(key in line.lower() for key in keys)
        ]

    @staticmethod
    def _build_trend(
        *, current: float | None, baseline: float | None, noise_floor_ms: float | None = None
    ) -> DashboardTrendIndicator:
        """Up, down or flat; a duration (``noise_floor_ms`` set) within timing noise is flat."""
        if current is None or baseline is None:
            return DashboardTrendIndicator(direction="unknown", delta_abs=None, delta_pct=None)
        delta_abs = current - baseline
        delta_pct = None if baseline == 0 else (delta_abs / baseline) * 100.0
        direction: TrendDirection
        if noise_floor_ms is not None and within_noise(
            absolute_delta=delta_abs, baseline=baseline, floor_ms=noise_floor_ms
        ):
            direction = "flat"
        elif delta_abs > 0:
            direction = "up"
        elif delta_abs < 0:
            direction = "down"
        else:
            direction = "flat"
        return DashboardTrendIndicator(
            direction=direction, delta_abs=delta_abs, delta_pct=delta_pct
        )

    @staticmethod
    def _pick_link(links: dict[str, str], *keys: str) -> DashboardReportLink:
        for key in keys:
            value = links.get(key)
            if value:
                return DashboardReportLink(url=value, state="available")
        return DashboardReportLink(url=None, state="missing")

    def _build_report_links(self, *, latest_session: RunSessionView | None) -> DashboardReportLinks:
        links = latest_session.links_under_static if latest_session else {}
        allure_base = os.getenv("ALLURE_SERVER_URL", "").rstrip("/")
        if latest_session and allure_base:
            allure = DashboardReportLink(
                url=f"{allure_base.rstrip('/')}/reports/{latest_session.run_id}/index.html",
                state="available",
            )
        elif allure_base:
            allure = DashboardReportLink(url=None, state="missing")
        else:
            allure = DashboardReportLink(url=None, state="unknown")
        behave = self._pick_link(links, "behave_native", "behavex", "behave")
        return DashboardReportLinks(allure=allure, behave=behave)

    @staticmethod
    def _build_recent_run(
        *, index: int, session: RunSessionView, sessions: list[RunSessionView]
    ) -> DashboardRecentRun:
        compare_url = None
        baseline = _previous_same_cycle(sessions, index)
        if baseline is not None:
            compare_url = (
                f"/compare?current_run_id={session.run_id}&baseline_run_id={baseline.run_id}"
            )
        return DashboardRecentRun(
            run_id=session.run_id,
            cycle=session.cycle,
            created_at=session.created_at,
            status=session.status.value if session.status is not None else None,
            returncode=session.returncode,
            health_pct=session.health_pct,
            duration_ms=None,
            run_detail_url=f"/runs/{session.run_id}",
            compare_url=compare_url,
            branch=session.branch,
            commit=session.commit,
        )
