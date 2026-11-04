from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from qasight.history.read_model import get_run
from qasight.history.views import CompletedRunView
from qasight.services.delta_models import (
    DeltaClassification,
    DeltaComparisonResult,
    DeltaDirection,
    DeltaStatusSummary,
    MetricDelta,
    StageDelta,
)

# Timing jitters between identical runs. A duration change counts only when it is
# both this large relative to the baseline and at least the metric's
# ``noise_floor_ms``; smaller ones are "neutral" with reason ``within_noise``.
NOISE_RELATIVE_PCT = 5.0


def within_noise(*, absolute_delta: float, baseline: float, floor_ms: float) -> bool:
    """True when a duration change is too small to mean anything (see ``NOISE_RELATIVE_PCT``)."""
    if abs(absolute_delta) < floor_ms:
        return True
    return baseline != 0 and abs(absolute_delta / baseline) * 100.0 < NOISE_RELATIVE_PCT


class DeltaComparisonError(ValueError):
    """Base domain exception for delta comparison failures."""


class InvalidRunIdError(DeltaComparisonError):
    """Raised when one of the run ids is invalid."""


class RunNotFoundComparisonError(DeltaComparisonError):
    """Raised when a requested run id does not exist."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"Run not found: {run_id}")
        self.run_id = run_id


class IncompatibleRunDataError(DeltaComparisonError):
    """Raised when a pair of runs cannot be compared safely."""


@dataclass(frozen=True)
class _MetricPolicy:
    key: str
    label: str
    group: Literal["reliability", "performance"]
    direction: DeltaDirection
    unit: Literal["ms", "pct", "tests"]
    # Smallest change in ms that counts as a regression or improvement (ms metrics only).
    noise_floor_ms: float = 0.0


class DeltaComparisonService:
    _METRIC_POLICY: tuple[_MetricPolicy, ...] = (
        _MetricPolicy("total_tests", "Total tests", "reliability", "higher_is_better", "tests"),
        _MetricPolicy("passed", "Passed tests", "reliability", "higher_is_better", "tests"),
        _MetricPolicy("failed", "Failed tests", "reliability", "lower_is_better", "tests"),
        _MetricPolicy("broken", "Broken tests", "reliability", "lower_is_better", "tests"),
        _MetricPolicy("skipped", "Skipped tests", "reliability", "lower_is_better", "tests"),
        _MetricPolicy("health_pct", "Health percentage", "reliability", "higher_is_better", "pct"),
        _MetricPolicy(
            "wall_duration_ms", "Wall duration", "performance", "lower_is_better", "ms", 100.0
        ),
        _MetricPolicy(
            "metrics_duration_ms", "Metrics duration", "performance", "lower_is_better", "ms", 100.0
        ),
        _MetricPolicy(
            "avg_case_ms", "Average case duration", "performance", "lower_is_better", "ms", 5.0
        ),
    )

    def __init__(
        self,
        *,
        run_lookup: Callable[[str], CompletedRunView | None] | None = None,
    ) -> None:
        self._run_lookup = run_lookup or (lambda run_id: get_run(run_id=run_id))

    def compare_runs(self, *, current_run_id: str, baseline_run_id: str) -> DeltaComparisonResult:
        cur_id = self._validate_run_id(current_run_id, label="current_run_id")
        base_id = self._validate_run_id(baseline_run_id, label="baseline_run_id")

        current = self._run_lookup(cur_id)
        if current is None:
            raise RunNotFoundComparisonError(cur_id)
        baseline = self._run_lookup(base_id)
        if baseline is None:
            raise RunNotFoundComparisonError(base_id)

        if current.test_kind != baseline.test_kind:
            raise IncompatibleRunDataError(
                "Cannot compare runs with different test kinds: "
                f"{current.test_kind!r} vs {baseline.test_kind!r}."
            )

        deltas = tuple(
            self._build_metric_delta(policy=policy, current=current, baseline=baseline)
            for policy in self._METRIC_POLICY
        )
        summary = self._build_status_summary(deltas)
        highlights = self._build_highlights(deltas)
        stage_deltas = self._build_stage_deltas(current=current, baseline=baseline)
        return DeltaComparisonResult(
            current_run_id=cur_id,
            baseline_run_id=base_id,
            current_test_kind=current.test_kind,
            baseline_test_kind=baseline.test_kind,
            metrics=deltas,
            status_summary=summary,
            highlights=highlights,
            stage_deltas=stage_deltas,
        )

    def _validate_run_id(self, run_id: str, *, label: str) -> str:
        cleaned = str(run_id).strip()
        if not cleaned:
            raise InvalidRunIdError(f"{label} must be a non-empty string.")
        return cleaned

    def _build_metric_delta(
        self,
        *,
        policy: _MetricPolicy,
        current: CompletedRunView,
        baseline: CompletedRunView,
    ) -> MetricDelta:
        current_value = self._to_float(getattr(current, policy.key, None))
        baseline_value = self._to_float(getattr(baseline, policy.key, None))

        if current_value is None:
            return self._unknown_delta(
                policy=policy,
                current=current_value,
                baseline=baseline_value,
                reason="missing_current_metric",
            )
        if baseline_value is None:
            return self._unknown_delta(
                policy=policy,
                current=current_value,
                baseline=baseline_value,
                reason="missing_baseline_metric",
            )

        absolute_delta = current_value - baseline_value
        relative_delta_pct: float | None = None
        reason: str | None = None
        if baseline_value == 0:
            reason = "zero_baseline_for_relative"
        else:
            relative_delta_pct = (absolute_delta / baseline_value) * 100.0

        classification = self._classify(absolute_delta=absolute_delta, direction=policy.direction)
        if (
            policy.unit == "ms"
            and classification != "neutral"
            and within_noise(
                absolute_delta=absolute_delta,
                baseline=baseline_value,
                floor_ms=policy.noise_floor_ms,
            )
        ):
            classification, reason = "neutral", "within_noise"
        return MetricDelta(
            metric_key=policy.key,
            label=policy.label,
            group=policy.group,
            current_value=current_value,
            baseline_value=baseline_value,
            absolute_delta=absolute_delta,
            relative_delta_pct=relative_delta_pct,
            classification=classification,
            reason=reason,
            direction=policy.direction,
            unit=policy.unit,
        )

    def _unknown_delta(
        self,
        *,
        policy: _MetricPolicy,
        current: float | None,
        baseline: float | None,
        reason: str,
    ) -> MetricDelta:
        return MetricDelta(
            metric_key=policy.key,
            label=policy.label,
            group=policy.group,
            current_value=current,
            baseline_value=baseline,
            absolute_delta=None,
            relative_delta_pct=None,
            classification="unknown",
            reason=reason,
            direction=policy.direction,
            unit=policy.unit,
        )

    def _build_stage_deltas(
        self,
        *,
        current: CompletedRunView,
        baseline: CompletedRunView,
    ) -> tuple[StageDelta, ...]:
        """Match stages by name across the two runs -- reuses stage_health already
        loaded on each `CompletedRunView`, no archive extraction needed."""

        current_by_name = {str(s.get("name")): s for s in current.stage_health if s.get("name")}
        baseline_by_name = {str(s.get("name")): s for s in baseline.stage_health if s.get("name")}
        names = sorted(set(current_by_name) | set(baseline_by_name))

        deltas: list[StageDelta] = []
        for name in names:
            cur_stage = current_by_name.get(name)
            base_stage = baseline_by_name.get(name)
            cur_health = self._to_float(cur_stage.get("health_pct")) if cur_stage else None
            base_health = self._to_float(base_stage.get("health_pct")) if base_stage else None

            health_delta: float | None = None
            classification: DeltaClassification = "unknown"
            if cur_health is not None and base_health is not None:
                health_delta = cur_health - base_health
                classification = self._classify(
                    absolute_delta=health_delta, direction="higher_is_better"
                )

            deltas.append(
                StageDelta(
                    stage_name=name,
                    framework=(cur_stage or base_stage or {}).get("framework"),
                    baseline_total_tests=(base_stage or {}).get("total_tests"),
                    current_total_tests=(cur_stage or {}).get("total_tests"),
                    baseline_passed=(base_stage or {}).get("passed"),
                    current_passed=(cur_stage or {}).get("passed"),
                    baseline_health_pct=base_health,
                    current_health_pct=cur_health,
                    health_pct_delta=health_delta,
                    classification=classification,
                )
            )
        return tuple(deltas)

    def _build_status_summary(self, deltas: tuple[MetricDelta, ...]) -> DeltaStatusSummary:
        regressions: list[str] = []
        improvements: list[str] = []
        unchanged: list[str] = []
        unknown: list[str] = []
        for delta in deltas:
            if delta.classification == "regression":
                regressions.append(delta.metric_key)
            elif delta.classification == "improvement":
                improvements.append(delta.metric_key)
            elif delta.classification == "neutral":
                unchanged.append(delta.metric_key)
            else:
                unknown.append(delta.metric_key)
        return DeltaStatusSummary(
            regressions=tuple(regressions),
            improvements=tuple(improvements),
            unchanged=tuple(unchanged),
            unknown=tuple(unknown),
        )

    def _build_highlights(self, deltas: tuple[MetricDelta, ...]) -> tuple[str, ...]:
        """Up to five plain sentences about the biggest changes, largest first.

        Durations read like the UI (``6.9 s``, ``265 ms``), counts carry the right
        plural, the pass rate is "points", and the metrics duration is left out
        when it moved exactly as much as the wall duration (every ingested run).
        """
        by_key = {d.metric_key: d for d in deltas}
        wall = by_key.get("wall_duration_ms")
        ranked = sorted(
            [
                d
                for d in deltas
                if d.classification in {"regression", "improvement"}
                and d.absolute_delta is not None
                and not (
                    d.metric_key == "metrics_duration_ms"
                    and wall is not None
                    and wall.absolute_delta == d.absolute_delta
                )
            ],
            key=lambda d: abs(d.absolute_delta or 0.0),
            reverse=True,
        )
        return tuple(self._highlight_sentence(d) for d in ranked[:5])

    @staticmethod
    def _highlight_sentence(delta: MetricDelta) -> str:
        change = delta.absolute_delta or 0.0
        magnitude = abs(change)
        if delta.unit == "ms":
            amount = f"{magnitude / 1000:.1f} s" if magnitude >= 1000 else f"{magnitude:.0f} ms"
            verb = "up" if change > 0 else "down"
            return f"{delta.label} {verb} {amount}."
        if delta.unit == "pct":
            verb = "up" if change > 0 else "down"
            return f"Pass rate {verb} {magnitude:.1f} points."
        count = int(round(magnitude))
        noun = "test" if count == 1 else "tests"
        subject = delta.label.removesuffix(" tests").lower()
        if delta.metric_key == "total_tests":
            return f"{count} {'more' if change > 0 else 'fewer'} {noun} ran."
        return f"{count} {'more' if change > 0 else 'fewer'} {noun} {subject}."

    @staticmethod
    def _to_float(value: float | int | None) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _classify(*, absolute_delta: float, direction: DeltaDirection) -> DeltaClassification:
        if absolute_delta == 0:
            return "neutral"
        if direction == "higher_is_better":
            return "improvement" if absolute_delta > 0 else "regression"
        return "improvement" if absolute_delta < 0 else "regression"
