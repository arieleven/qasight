from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .metrics import RunMetrics, parse_allure_results_dir

logger = logging.getLogger(__name__)

TrendDirection = Literal["improving", "declining", "flat", "unknown"]


@dataclass(frozen=True)
class ExtractedMetrics:
    """Unified metrics from Allure report summary and/or raw results."""

    total_tests: int
    passed: int
    failed: int
    broken: int
    skipped: int
    unknown: int
    duration_ms: int
    timestamp: int
    source: str  # "summary_json" | "results_dir"

    def success_rate_pct(self) -> float:
        if self.total_tests <= 0:
            return 0.0
        return (self.passed / self.total_tests) * 100.0


def _parse_statistic_block(stat: dict[str, Any]) -> dict[str, int]:
    return {
        "total": int(stat.get("total") or 0),
        "passed": int(stat.get("passed") or 0),
        "failed": int(stat.get("failed") or 0),
        "broken": int(stat.get("broken") or 0),
        "skipped": int(stat.get("skipped") or 0),
        "unknown": int(stat.get("unknown") or 0),
    }


def _duration_from_summary(data: dict[str, Any]) -> int:
    time_block = data.get("time")
    if not isinstance(time_block, dict):
        return 0
    # Allure often exposes sumDuration, maxDuration, duration (ms)
    for key in ("sumDuration", "duration", "maxDuration"):
        v = time_block.get(key)
        if isinstance(v, (int, float)) and v >= 0:
            return int(v)
    return 0


def extract_from_summary_json(*, summary_path: Path) -> ExtractedMetrics | None:
    """
    Parse ``allure-report/widgets/summary.json`` (or equivalent) produced by ``allure generate``.
    """
    summary_path = summary_path.expanduser().resolve()
    if not summary_path.is_file():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("unreadable Allure summary %s", summary_path, exc_info=True)
        return None

    stat = data.get("statistic") if isinstance(data, dict) else None
    if not isinstance(stat, dict):
        return None

    s = _parse_statistic_block(stat)
    total = s["total"] or (s["passed"] + s["failed"] + s["broken"] + s["skipped"] + s["unknown"])
    duration_ms = _duration_from_summary(data)
    ts = int(time.time())

    return ExtractedMetrics(
        total_tests=total,
        passed=s["passed"],
        failed=s["failed"],
        broken=s["broken"],
        skipped=s["skipped"],
        unknown=s["unknown"],
        duration_ms=duration_ms,
        timestamp=ts,
        source="summary_json",
    )


def extract_from_report_dir(*, report_dir: Path) -> ExtractedMetrics | None:
    """Prefer ``widgets/summary.json`` under the generated Allure HTML report directory."""
    report_dir = report_dir.expanduser().resolve()
    candidates = [
        report_dir / "widgets" / "summary.json",
        report_dir / "data" / "summary.json",
    ]
    for p in candidates:
        m = extract_from_summary_json(summary_path=p)
        if m is not None:
            return m
    return None


def extract_from_results_dir(*, results_dir: Path) -> ExtractedMetrics:
    """Fallback: aggregate from ``*-result.json`` (same semantics as :func:`parse_allure_results_dir`)."""
    rm = parse_allure_results_dir(results_dir)
    return ExtractedMetrics(
        total_tests=rm.total_tests,
        passed=rm.passed,
        failed=rm.failed,
        broken=rm.broken,
        skipped=rm.skipped,
        unknown=rm.unknown,
        duration_ms=rm.duration_ms,
        timestamp=rm.timestamp,
        source="results_dir",
    )


def extract_best(
    *,
    report_dir: Path | None = None,
    results_dir: Path | None = None,
) -> ExtractedMetrics | None:
    """
    Prefer summary JSON from a generated report; otherwise parse raw Allure results.
    """
    if report_dir is not None:
        m = extract_from_report_dir(report_dir=report_dir)
        if m is not None:
            return m
    if results_dir is not None and results_dir.expanduser().resolve().is_dir():
        try:
            return extract_from_results_dir(results_dir=results_dir)
        except OSError:
            logger.warning("could not read Allure results under %s", results_dir, exc_info=True)
            return None
    return None


def to_run_metrics(em: ExtractedMetrics, *, run_id: str | None = None) -> RunMetrics:
    return RunMetrics(
        timestamp=em.timestamp,
        total_tests=em.total_tests,
        passed=em.passed,
        failed=em.failed,
        broken=em.broken,
        skipped=em.skipped,
        unknown=em.unknown,
        duration_ms=em.duration_ms,
        run_id=run_id,
    )


def success_rate_percentage(em: ExtractedMetrics) -> float:
    """Alias for UI: pass rate as percent."""
    return em.success_rate_pct()


def trend_direction(
    history_rates: list[float],
    *,
    epsilon: float = 0.01,
) -> TrendDirection:
    """
    Compare the last success-rate to the previous one.

    ``history_rates`` oldest → newest (e.g. from recent DB rows or extractions).
    """
    if len(history_rates) < 2:
        return "unknown"
    prev, cur = history_rates[-2], history_rates[-1]
    if cur > prev + epsilon:
        return "improving"
    if cur < prev - epsilon:
        return "declining"
    return "flat"


def trend_from_extracted_history(rows: list[ExtractedMetrics]) -> TrendDirection:
    rates = [r.success_rate_pct() for r in rows]
    return trend_direction(rates)
