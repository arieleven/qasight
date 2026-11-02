from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from testo_api.models import (
    AttachmentItem,
    BrokenTestItem,
    CycleLabelsResponse,
    CycleTestsResponse,
    DeltaCaseChange,
    DeltaCaseChangesResponse,
    DeltaComparisonMeta,
    DeltaComparisonResponse,
    DeltaMetricNode,
    DeltaMetricsResponse,
    DeltaPerformanceMetrics,
    DeltaReliabilityMetrics,
    DeltaStageDelta,
    DeltaStatusSummaryResponse,
    EnvironmentOutcomeItem,
    FlakyTestItem,
    FlakyTestsResponse,
    LabelCountItem,
    TestHistoryResponse,
    TestPointItem,
    TestSummaryItem,
)
from testo_core.config.schema import QuarantineRule
from testo_core.history.read_model import get_run
from testo_core.services.delta_models import MetricDelta
from testo_core.services.delta_service import (
    DeltaComparisonService,
    IncompatibleRunDataError,
    InvalidRunIdError,
    RunNotFoundComparisonError,
)
from testo_core.services.flaky_service import DEFAULT_WINDOW, FlakyTest, find_flaky_tests
from testo_core.services.run_snapshot_diff import diff_run_snapshots
from testo_core.services.test_attachments import attachments_for
from testo_core.services.test_history import cycle_source, list_cycle_tests, load_test_history
from testo_core.services.test_labels import cycle_labels
from testo_core.services.test_ownership import issue_url, owners_for

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["analytics"])


@router.get("/analytics/delta", response_model=DeltaComparisonResponse)
def get_delta_comparison(current_run_id: str, baseline_run_id: str) -> DeltaComparisonResponse:
    service = DeltaComparisonService()
    try:
        result = service.compare_runs(
            current_run_id=current_run_id, baseline_run_id=baseline_run_id
        )
    except InvalidRunIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RunNotFoundComparisonError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IncompatibleRunDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    metric_map = {metric.metric_key: metric for metric in result.metrics}
    return DeltaComparisonResponse(
        comparison=DeltaComparisonMeta(
            current_run_id=result.current_run_id,
            baseline_run_id=result.baseline_run_id,
            current_test_kind=result.current_test_kind,
            baseline_test_kind=result.baseline_test_kind,
        ),
        metrics=DeltaMetricsResponse(
            reliability=DeltaReliabilityMetrics(
                total_tests=_to_metric_node(metric_map["total_tests"]),
                passed=_to_metric_node(metric_map["passed"]),
                failed=_to_metric_node(metric_map["failed"]),
                broken=_to_metric_node(metric_map["broken"]),
                skipped=_to_metric_node(metric_map["skipped"]),
                health_pct=_to_metric_node(metric_map["health_pct"]),
            ),
            performance=DeltaPerformanceMetrics(
                wall_duration_ms=_to_metric_node(metric_map["wall_duration_ms"]),
                metrics_duration_ms=_to_metric_node(metric_map["metrics_duration_ms"]),
                avg_case_ms=_to_metric_node(metric_map["avg_case_ms"]),
            ),
        ),
        status_summary=DeltaStatusSummaryResponse(
            regressions=list(result.status_summary.regressions),
            improvements=list(result.status_summary.improvements),
            unchanged=list(result.status_summary.unchanged),
            unknown=list(result.status_summary.unknown),
        ),
        highlights=list(result.highlights),
        stage_deltas=[
            DeltaStageDelta(
                stage_name=sd.stage_name,
                framework=sd.framework,
                baseline_total_tests=sd.baseline_total_tests,
                current_total_tests=sd.current_total_tests,
                baseline_passed=sd.baseline_passed,
                current_passed=sd.current_passed,
                baseline_health_pct=sd.baseline_health_pct,
                current_health_pct=sd.current_health_pct,
                health_pct_delta=sd.health_pct_delta,
                classification=sd.classification,
            )
            for sd in result.stage_deltas
        ],
    )


def _to_metric_node(metric: MetricDelta) -> DeltaMetricNode:
    return DeltaMetricNode(
        current_value=metric.current_value,
        baseline_value=metric.baseline_value,
        absolute_delta=metric.absolute_delta,
        relative_delta_pct=metric.relative_delta_pct,
        classification=metric.classification,
        reason=metric.reason,
        direction=metric.direction,
        unit=metric.unit,
    )


@router.get("/analytics/delta/cases", response_model=DeltaCaseChangesResponse)
def get_delta_case_changes(current_run_id: str, baseline_run_id: str) -> DeltaCaseChangesResponse:
    """Per-test added/removed/regression/fix breakdown -- UI parity with ``testo diff``.

    Computed on-demand from each run's own artifact snapshot every request (no
    caching): extracts both snapshots into a temp dir and matches cases by
    historyId/fullName/uuid, same as the CLI's full (non ``--metrics-only``) diff.
    """
    import tempfile
    from pathlib import Path

    current = get_run(run_id=current_run_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {current_run_id}")
    baseline = get_run(run_id=baseline_run_id)
    if baseline is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {baseline_run_id}")

    with tempfile.TemporaryDirectory(prefix="testo-api-diff-") as td:
        changes = diff_run_snapshots(baseline=baseline, current=current, tmp=Path(td))

    return DeltaCaseChangesResponse(
        current_run_id=current_run_id,
        baseline_run_id=baseline_run_id,
        changes=[
            DeltaCaseChange(
                key=c.key,
                name=c.name,
                group=c.group,
                baseline_status=c.baseline_status,
                current_status=c.current_status,
                kind=c.kind,
                duration_delta_ms=c.duration_delta_ms,
            )
            for c in changes
        ],
    )


def flaky_quarantine(cycle: str, flaky: tuple[FlakyTest, ...]) -> dict[str, str | None]:
    """``key -> stored entry`` of each flaky test the cycle's quarantine matches.

    The entry is the stored test name the UI can release, or None when only a
    glob or a ``testosterone.yaml`` rule matches. Config is read when this
    process can find it (a team server often can't); the stored list is enough there.
    """
    from testo_core.config.errors import ConfigError
    from testo_core.config.loader import discover_and_load
    from testo_core.engine.quarantine import matching_rule
    from testo_core.services.quarantine_store import QuarantineStore, merge_rules

    exact: dict[str, str] = {}
    stored: tuple[QuarantineRule, ...] = ()
    try:
        for entry in QuarantineStore().list(cycle):
            stored += (entry.rule(),)
            if not entry.is_glob:
                exact[entry.test] = entry.test
    except Exception:  # noqa: BLE001 - the flaky list still shows without it
        logger.warning("could not read the quarantine list of %s", cycle, exc_info=True)
    configured: tuple[QuarantineRule, ...] = ()
    try:
        plan = discover_and_load().cycles.get(cycle)
        configured = plan.quarantine if plan is not None else ()
    except ConfigError:
        pass
    rules = merge_rules(stored, configured)
    out: dict[str, str | None] = {}
    for t in flaky:
        if matching_rule(rules, name=t.name, full_name=t.full_name) is not None:
            out[t.key] = exact.get(t.name) or exact.get(t.full_name)
    return out


@router.get("/analytics/flaky", response_model=FlakyTestsResponse)
def get_flaky_tests(
    cycle: str, window: int = Query(DEFAULT_WINDOW, ge=2, le=200)
) -> FlakyTestsResponse:
    """Flaky tests in a cycle's last runs (retries, same-commit results, flips per branch).

    Tests that failed every one of their last runs come back as ``broken`` instead.

    UI parity with ``testo flaky --cycle``; see ``testo_core/services/flaky_service.py``.
    """
    report = find_flaky_tests(cycle=cycle, window=window)
    checkout, repository = cycle_source(cycle)
    quarantine = flaky_quarantine(cycle, report.flaky)
    owners = {t.key: owners_for(checkout, t.full_name, t.group) for t in report.flaky}
    return FlakyTestsResponse(
        cycle=report.cycle,
        window=report.window,
        runs_analyzed=report.runs_analyzed,
        tests_analyzed=report.tests_analyzed,
        items=[
            FlakyTestItem(
                key=t.key,
                name=t.name,
                group=t.group,
                runs=t.runs,
                passed=t.passed,
                failed=t.failed,
                flips=t.flips,
                flip_rate=t.flip_rate,
                last_status=t.last_status,
                history=list(t.history),
                owners=list(owners[t.key]),
                issue_url=issue_url(
                    repository,
                    cycle=cycle,
                    test_name=t.name,
                    full_name=t.full_name,
                    owners=owners[t.key],
                ),
                reasons=list(t.reasons),
                retried=t.retried,
                quarantined=t.key in quarantine,
                quarantine_entry=quarantine.get(t.key),
            )
            for t in report.flaky
        ],
        broken=[
            BrokenTestItem(
                key=b.key,
                name=b.name,
                group=b.group,
                failing_runs=b.failing_runs,
                message=b.message,
            )
            for b in report.broken
        ],
    )


@router.get("/analytics/tests", response_model=CycleTestsResponse)
def get_cycle_tests(
    cycle: str,
    q: str | None = Query(
        None, max_length=200, description="Match in name, full name, group or a label's value."
    ),
    label: str | None = Query(
        None,
        max_length=200,
        description="Only tests with this label: `name:value` (`feature:Checkout`) or a bare value.",
    ),
    window: int = Query(DEFAULT_WINDOW, ge=1, le=200),
) -> CycleTestsResponse:
    """Every test in a cycle's last runs: outcomes, flaky or not, durations and slowdowns."""
    found = list_cycle_tests(cycle, window=window, query=q, label=label)
    return CycleTestsResponse(
        cycle=found.cycle,
        runs_considered=found.runs_considered,
        items=[TestSummaryItem(**vars(t)) for t in found.tests],
    )


@router.get("/analytics/labels", response_model=CycleLabelsResponse)
def get_cycle_labels(
    cycle: str, window: int = Query(DEFAULT_WINDOW, ge=1, le=200)
) -> CycleLabelsResponse:
    """A cycle's tests counted by label (feature, story, severity, owner, tag...).

    Each label's tests, how many failed in their latest run and how many are flaky,
    features first. See ``testo_core/services/test_labels.py``.
    """
    found = cycle_labels(cycle, window=window)
    return CycleLabelsResponse(
        cycle=found.cycle,
        runs_considered=found.runs_considered,
        labelled_tests=found.labelled_tests,
        total_tests=found.total_tests,
        items=[LabelCountItem(**vars(c), filter=c.filter) for c in found.labels],
    )


@router.get("/analytics/test-history", response_model=TestHistoryResponse)
def get_test_history(
    cycle: str,
    key: str = Query(..., max_length=500),
    window: int = Query(DEFAULT_WINDOW, ge=1, le=200),
) -> TestHistoryResponse:
    """One test in each of a cycle's last runs, newest first, with its last failure."""
    history = load_test_history(cycle, key, window=window)
    if history is None:
        raise HTTPException(status_code=404, detail=f"No run of {cycle} has the test {key}")
    last = history.last_failure
    summary = history.summary
    checkout, repository = cycle_source(cycle)
    owners = owners_for(checkout, summary.full_name, summary.group)
    failed_run = get_run(run_id=last.run_id) if last else None
    attachments = attachments_for(failed_run, key) if failed_run else ()
    return TestHistoryResponse(
        cycle=history.cycle,
        test=TestSummaryItem(**vars(history.summary)),
        points=[TestPointItem(**vars(p)) for p in history.points],
        last_failure=TestPointItem(**vars(last)) if last else None,
        last_failure_attachments=[AttachmentItem(**vars(a)) for a in attachments],
        environments=[EnvironmentOutcomeItem(**vars(e)) for e in history.environments],
        owners=list(owners),
        issue_url=issue_url(
            repository,
            cycle=cycle,
            test_name=summary.name,
            full_name=summary.full_name,
            message=last.message if last else "",
            owners=owners,
        ),
    )
