"""Real per-stage + overall health % for a completed :class:`PlanResult`.

Reuses the Allure-parsing machinery the reporting pipeline already has
(:func:`qasight.reporting.allure_results.parse_collected_results`) instead
of approximating health from stage subprocess return codes.
"""

from __future__ import annotations

from pathlib import Path

from qasight.engine.result import PlanResult
from qasight.reporting.allure_results import parse_collected_results
from qasight.reporting.collector import CollectedResults, StageCollection


def _pct(passed: int, total: int) -> float | None:
    return 100.0 * passed / total if total else None


def compute_stage_health(
    result: PlanResult, artifacts_root: Path
) -> tuple[list[dict], float | None]:
    """Return (per-stage health dicts, overall weighted health_pct).

    Each stage dict has ``total_tests``/``passed``/``failed``/``broken``/
    ``skipped``/``health_pct``/``test_time_ms`` keyed by ``name`` so callers can merge it into
    their existing per-stage metadata. The overall figure is a single
    weighted pass rate (sum of passed / sum of total across every stage),
    not an average of per-stage percentages. Returns ``(per_stage, None)``
    when no stage produced any parseable Allure results, so callers can fall
    back to a returncode-based estimate instead of reporting a misleading 0%.

    A stage that failed without producing any results (e.g. it crashed at
    startup) has no pass rate of its own, and leaving it out would report a
    broken cycle as 100% healthy. The overall pass rate is therefore scaled
    by the share of stages that did not crash this way: one healthy stage and
    one crashed stage give 50%.
    """
    collected = CollectedResults(
        artifacts_root=artifacts_root,
        stages=[
            StageCollection(
                plan=result.plan_name,
                stage=s.stage_name,
                framework=s.framework,
                # Layout written by executor.run_stage(): <stage_dir>/allure-results/<framework>/*-result.json
                results_dir=s.artifacts_dir / "allure-results" / s.framework,
                log_path=s.log_path,
            )
            for s in result.stages
        ],
    )
    aggregate = parse_collected_results(collected)

    per_stage: list[dict] = [
        {
            "name": s.stage,
            "total_tests": s.total,
            "passed": s.passed,
            "failed": s.failed,
            "broken": s.broken,
            "skipped": s.skipped,
            "health_pct": _pct(s.passed, s.total),
            "test_time_ms": s.duration_ms,
        }
        for s in aggregate.stages
    ]
    overall_health_pct = _pct(aggregate.passed, aggregate.total)
    if overall_health_pct is None:
        return per_stage, None
    tests_by_stage = {s.stage: s.total for s in aggregate.stages}
    crashed = sum(
        1 for s in result.stages if s.returncode != 0 and not tests_by_stage.get(s.stage_name)
    )
    return per_stage, overall_health_pct * (len(result.stages) - crashed) / len(result.stages)


def run_test_totals(stage_health: list[dict]) -> dict[str, int | float | None]:
    """Run-level test counts and test time summed from :func:`compute_stage_health` rows.

    ``metrics_duration_ms`` is the sum of per-test durations (not wall time);
    ``avg_case_ms`` divides it by ``total_tests``. Every value is ``None`` when
    no stage produced Allure results, and ``avg_case_ms`` is ``None`` when the
    run has no tests, so the read side shows "n/a" rather than a fake 0.
    """
    if not stage_health:
        return dict.fromkeys(
            (
                "total_tests",
                "passed",
                "failed",
                "broken",
                "skipped",
                "metrics_duration_ms",
                "avg_case_ms",
            )
        )
    total = sum(h["total_tests"] for h in stage_health)
    test_time_ms = sum(h["test_time_ms"] for h in stage_health)
    return {
        "total_tests": total,
        "passed": sum(h["passed"] for h in stage_health),
        "failed": sum(h["failed"] for h in stage_health),
        "broken": sum(h["broken"] for h in stage_health),
        "skipped": sum(h["skipped"] for h in stage_health),
        "metrics_duration_ms": test_time_ms,
        "avg_case_ms": test_time_ms / total if total else None,
    }
