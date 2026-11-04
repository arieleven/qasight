from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DeltaClassification = Literal["regression", "improvement", "neutral", "unknown"]
DeltaDirection = Literal["higher_is_better", "lower_is_better"]


@dataclass(frozen=True)
class MetricDelta:
    metric_key: str
    label: str
    group: Literal["reliability", "performance"]
    current_value: float | None
    baseline_value: float | None
    absolute_delta: float | None
    relative_delta_pct: float | None
    classification: DeltaClassification
    reason: str | None
    direction: DeltaDirection
    unit: Literal["ms", "pct", "tests"]

    def is_unknown(self) -> bool:
        return self.classification == "unknown"


@dataclass(frozen=True)
class DeltaStatusSummary:
    regressions: tuple[str, ...]
    improvements: tuple[str, ...]
    unchanged: tuple[str, ...]
    unknown: tuple[str, ...]


@dataclass(frozen=True)
class StageDelta:
    """Per-stage health comparison, matched by stage name (no archive I/O)."""

    stage_name: str
    framework: str | None
    baseline_total_tests: int | None
    current_total_tests: int | None
    baseline_passed: int | None
    current_passed: int | None
    baseline_health_pct: float | None
    current_health_pct: float | None
    health_pct_delta: float | None
    classification: DeltaClassification


@dataclass(frozen=True)
class DeltaComparisonResult:
    current_run_id: str
    baseline_run_id: str
    current_test_kind: str
    baseline_test_kind: str
    metrics: tuple[MetricDelta, ...]
    status_summary: DeltaStatusSummary
    highlights: tuple[str, ...]
    stage_deltas: tuple[StageDelta, ...] = ()
