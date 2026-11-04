from __future__ import annotations

import pytest

from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services.delta_service import (
    DeltaComparisonService,
    IncompatibleRunDataError,
    InvalidRunIdError,
    RunNotFoundComparisonError,
)


def _view(
    *,
    run_id: str,
    test_kind: str = "pytest",
    total_tests: int | None = 10,
    passed: int | None = 10,
    failed: int | None = 0,
    broken: int | None = 0,
    skipped: int | None = 0,
    health_pct: float | None = 100.0,
    wall_duration_ms: float = 1000.0,
    metrics_duration_ms: int | None = 900,
    avg_case_ms: float | None = 100.0,
    stage_health: list[dict] | None = None,
) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind=test_kind,
        returncode=0,
        wall_duration_ms=wall_duration_ms,
        metrics_duration_ms=metrics_duration_ms,
        total_tests=total_tests,
        passed=passed,
        failed=failed,
        broken=broken,
        skipped=skipped,
        avg_case_ms=avg_case_ms,
        health_pct=health_pct,
        target_repo="/tmp/repo",
        snapshot_dir=None,
        audit_json=None,
        stage_health=stage_health or [],
    )


def _service(rows: dict[str, CompletedRunView | None]) -> DeltaComparisonService:
    return DeltaComparisonService(run_lookup=lambda run_id: rows.get(run_id))


def test_compare_runs_classifies_regressions_and_improvements() -> None:
    service = _service(
        {
            "current": _view(
                run_id="current", failed=3, broken=1, wall_duration_ms=1300.0, health_pct=90.0
            ),
            "baseline": _view(
                run_id="baseline", failed=1, broken=0, wall_duration_ms=1000.0, health_pct=96.0
            ),
        }
    )
    result = service.compare_runs(current_run_id="current", baseline_run_id="baseline")

    metrics = {metric.metric_key: metric for metric in result.metrics}
    assert metrics["failed"].classification == "regression"
    assert metrics["failed"].absolute_delta == 2.0
    assert metrics["wall_duration_ms"].classification == "regression"
    assert metrics["health_pct"].classification == "regression"
    assert "failed" in result.status_summary.regressions
    assert result.highlights


def test_compare_runs_supports_improvement_and_neutral() -> None:
    service = _service(
        {
            "current": _view(run_id="current", failed=0, wall_duration_ms=900.0, passed=9),
            "baseline": _view(run_id="baseline", failed=2, wall_duration_ms=1200.0, passed=9),
        }
    )
    result = service.compare_runs(current_run_id="current", baseline_run_id="baseline")
    metrics = {metric.metric_key: metric for metric in result.metrics}

    assert metrics["failed"].classification == "improvement"
    assert metrics["passed"].classification == "neutral"
    assert metrics["wall_duration_ms"].classification == "improvement"
    assert "passed" in result.status_summary.unchanged


def test_compare_runs_marks_unknown_for_missing_metrics() -> None:
    service = _service(
        {
            "current": _view(run_id="current", metrics_duration_ms=None),
            "baseline": _view(run_id="baseline", metrics_duration_ms=1000),
        }
    )
    result = service.compare_runs(current_run_id="current", baseline_run_id="baseline")
    metrics = {metric.metric_key: metric for metric in result.metrics}

    assert metrics["metrics_duration_ms"].classification == "unknown"
    assert metrics["metrics_duration_ms"].reason == "missing_current_metric"
    assert "metrics_duration_ms" in result.status_summary.unknown


def test_compare_runs_sets_relative_reason_when_baseline_zero() -> None:
    service = _service(
        {
            "current": _view(run_id="current", failed=1),
            "baseline": _view(run_id="baseline", failed=0),
        }
    )
    result = service.compare_runs(current_run_id="current", baseline_run_id="baseline")
    metrics = {metric.metric_key: metric for metric in result.metrics}

    assert metrics["failed"].absolute_delta == 1.0
    assert metrics["failed"].relative_delta_pct is None
    assert metrics["failed"].reason == "zero_baseline_for_relative"


def test_compare_runs_rejects_incompatible_test_kinds() -> None:
    service = _service(
        {
            "current": _view(run_id="current", test_kind="pytest"),
            "baseline": _view(run_id="baseline", test_kind="behavex"),
        }
    )
    with pytest.raises(IncompatibleRunDataError):
        service.compare_runs(current_run_id="current", baseline_run_id="baseline")


def test_compare_runs_rejects_invalid_run_id() -> None:
    service = _service({})
    with pytest.raises(InvalidRunIdError):
        service.compare_runs(current_run_id=" ", baseline_run_id="baseline")


def test_compare_runs_raises_when_run_missing() -> None:
    service = _service({"baseline": _view(run_id="baseline")})
    with pytest.raises(RunNotFoundComparisonError):
        service.compare_runs(current_run_id="current", baseline_run_id="baseline")


def test_compare_runs_matches_stage_deltas_by_name() -> None:
    service = _service(
        {
            "current": _view(
                run_id="current",
                stage_health=[
                    {
                        "name": "pytest-sample",
                        "framework": "pytest",
                        "total_tests": 10,
                        "passed": 8,
                        "health_pct": 80.0,
                    }
                ],
            ),
            "baseline": _view(
                run_id="baseline",
                stage_health=[
                    {
                        "name": "pytest-sample",
                        "framework": "pytest",
                        "total_tests": 10,
                        "passed": 10,
                        "health_pct": 100.0,
                    }
                ],
            ),
        }
    )
    result = service.compare_runs(current_run_id="current", baseline_run_id="baseline")

    assert len(result.stage_deltas) == 1
    stage = result.stage_deltas[0]
    assert stage.stage_name == "pytest-sample"
    assert stage.health_pct_delta == -20.0
    assert stage.classification == "regression"


def test_compare_runs_stage_delta_unknown_when_stage_missing_on_one_side() -> None:
    service = _service(
        {
            "current": _view(
                run_id="current",
                stage_health=[
                    {"name": "new-stage", "total_tests": 5, "passed": 5, "health_pct": 100.0}
                ],
            ),
            "baseline": _view(run_id="baseline", stage_health=[]),
        }
    )
    result = service.compare_runs(current_run_id="current", baseline_run_id="baseline")

    assert len(result.stage_deltas) == 1
    stage = result.stage_deltas[0]
    assert stage.stage_name == "new-stage"
    assert stage.baseline_health_pct is None
    assert stage.current_health_pct == 100.0
    assert stage.classification == "unknown"


def test_compare_runs_no_stage_deltas_when_no_stage_health() -> None:
    service = _service(
        {
            "current": _view(run_id="current"),
            "baseline": _view(run_id="baseline"),
        }
    )
    result = service.compare_runs(current_run_id="current", baseline_run_id="baseline")
    assert result.stage_deltas == ()


@pytest.mark.parametrize(
    ("baseline_ms", "current_ms", "expected"),
    [
        (6520.0, 6430.0, "neutral"),  # -90 ms, -1.4%: jitter
        (3410.0, 3420.0, "neutral"),  # +10 ms, +0.3%
        (1000.0, 1080.0, "neutral"),  # +8% but under the 100 ms floor
        (10_000.0, 10_300.0, "neutral"),  # +300 ms but only +3%
        (1000.0, 1200.0, "regression"),  # +200 ms, +20%
        (2000.0, 1500.0, "improvement"),  # -500 ms, -25%
    ],
)
def test_duration_changes_within_timing_noise_are_neutral(
    baseline_ms: float, current_ms: float, expected: str
) -> None:
    service = _service(
        {
            "base": _view(run_id="base", wall_duration_ms=baseline_ms),
            "cur": _view(run_id="cur", wall_duration_ms=current_ms),
        }
    )
    result = service.compare_runs(current_run_id="cur", baseline_run_id="base")
    wall = next(m for m in result.metrics if m.metric_key == "wall_duration_ms")
    assert wall.classification == expected
    assert (wall.reason == "within_noise") is (expected == "neutral")
    # The numbers are still reported; only the verdict changes.
    assert wall.absolute_delta == pytest.approx(current_ms - baseline_ms)


def test_average_case_duration_has_its_own_small_floor() -> None:
    service = _service(
        {
            "base": _view(run_id="base", avg_case_ms=20.0),
            "cur": _view(run_id="cur", avg_case_ms=30.0),  # +10 ms, +50%: real
        }
    )
    result = service.compare_runs(current_run_id="cur", baseline_run_id="base")
    avg = next(m for m in result.metrics if m.metric_key == "avg_case_ms")
    assert avg.classification == "regression"


def test_highlights_read_as_plain_sentences() -> None:
    """Durations like the UI, the right plural, pass rate in points, no duplicate
    line for a metrics duration that moved exactly as much as the wall duration."""
    service = _service(
        {
            "current": _view(
                run_id="current",
                passed=22,
                failed=1,
                health_pct=84.6,
                wall_duration_ms=32250.0,
                metrics_duration_ms=32250,
                avg_case_ms=1240.0,
            ),
            "baseline": _view(
                run_id="baseline",
                passed=21,
                failed=2,
                health_pct=80.8,
                wall_duration_ms=25350.0,
                metrics_duration_ms=25350,
                avg_case_ms=975.0,
            ),
        }
    )
    result = service.compare_runs(current_run_id="current", baseline_run_id="baseline")

    assert result.highlights == (
        "Wall duration up 6.9 s.",
        "Average case duration up 265 ms.",
        "Pass rate up 3.8 points.",
        "1 more test passed.",
        "1 fewer test failed.",
    )
    assert not any("Metrics duration" in line for line in result.highlights)
