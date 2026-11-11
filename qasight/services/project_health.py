"""Project health: a repeatable score for the tests of the project QASight tests.

Not a score of QASight itself. Each signal is computed from data the
history already has, the same data the dashboard, the cycle page and the flaky
card show, so the page never says something those pages don't:

* **Passing tests**: share of tests passing in each cycle's latest run.
* **Green builds**: share of each cycle's recent runs that passed.
* **Stable tests**: share of tests that are not flaky
  (:mod:`~qasight.services.flaky_service`).
* **Speed trend**: share of tests that did not get slower
  (:mod:`~qasight.services.test_history`).
* **Test hygiene**: share of tests neither skipped nor quarantined.
* **Test pyramid**: the shape of the unit / integration / e2e tiers
  (:mod:`~qasight.reporting.pyramid_viz`).

The overall score is the weighted mean of the signals that have data. The same
history always gives the same score: no AI is involved. The optional AI review
lives in :mod:`~qasight.services.project_review`.
"""

from __future__ import annotations

from dataclasses import dataclass

from qasight.history.read_model import latest_run_per_cycle, list_cycle_runs
from qasight.history.views import CompletedRunView
from qasight.reporting.pyramid_data import build_pyramid_model
from qasight.reporting.pyramid_viz import PyramidModel, PyramidShape, classify_shape
from qasight.repository.models import RunStatus
from qasight.services import test_history
from qasight.services.test_history import TestSummary

# Runs per cycle that "Green builds" looks at.
BUILD_WINDOW = 20
# Items listed per kind in "What to fix first".
MAX_FINDINGS = 5
_FAILED = frozenset({"failed", "broken"})

# How hard each share of bad tests pulls its signal down: 2% flaky tests cost
# 20 points, 2% slower tests 10, 10% skipped or quarantined tests 30.
_FLAKY_PENALTY = 1000
_SLOWER_PENALTY = 500
_HYGIENE_PENALTY = 300

_PYRAMID_SCORES = {
    PyramidShape.HEALTHY: 100,
    PyramidShape.IRREGULAR: 70,
    PyramidShape.MID_BULGE: 60,
    PyramidShape.TOP_HEAVY: 40,
}


@dataclass(frozen=True)
class HealthSignal:
    key: str
    name: str
    # 0-100; None when there is no data for it (then it doesn't count).
    score: int | None
    weight: int
    # The measured value in words, e.g. "47 of 50 tests".
    value: str
    # How it is measured, one sentence.
    how: str


@dataclass(frozen=True)
class CycleHealth:
    cycle: str
    run_id: str
    created_at: float
    passed_latest: bool
    tests: int
    failing: int
    flaky: int
    slower: int
    green_runs: int
    runs: int
    score: int | None


@dataclass(frozen=True)
class HealthFinding:
    kind: str  # "red_cycle" | "flaky" | "slower" | "quarantined" | "skipped"
    cycle: str
    title: str
    detail: str
    test_key: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class ProjectHealth:
    scope: str | None
    score: int | None
    grade: str | None
    cycles: int
    runs_considered: int
    tests: int
    latest_run_at: float | None
    signals: tuple[HealthSignal, ...]
    by_cycle: tuple[CycleHealth, ...]
    findings: tuple[HealthFinding, ...]


def grade_for(score: int | None) -> str | None:
    """A school grade for a 0-100 score."""
    if score is None:
        return None
    for floor, grade in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= floor:
            return grade
    return "F"


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _share_score(bad: int, total: int, penalty: int) -> int | None:
    return _clamp(100 - penalty * bad / total) if total else None


def _failed(run: CompletedRunView) -> bool:
    return run.returncode != 0 or run.status == RunStatus.FAILED


def _quarantined(run: CompletedRunView) -> list[str]:
    names: list[str] = []
    for stage in run.stage_health:
        names += [str(n) for n in stage.get("quarantined") or ()]
    return sorted(set(names))


def _weighted(signals: list[HealthSignal]) -> int | None:
    scored = [s for s in signals if s.score is not None]
    weight = sum(s.weight for s in scored)
    if not weight:
        return None
    return _clamp(sum((s.score or 0) * s.weight for s in scored) / weight)


@dataclass(frozen=True)
class _CycleData:
    run: CompletedRunView
    history: list[CompletedRunView]
    tests: tuple[TestSummary, ...]
    quarantined: list[str]


def _signals(data: list[_CycleData]) -> list[HealthSignal]:
    """The six signals over the given cycles' data."""
    passed = sum(d.run.passed or 0 for d in data)
    failed = sum((d.run.failed or 0) + (d.run.broken or 0) for d in data)
    skipped = sum(d.run.skipped or 0 for d in data)
    executed = passed + failed
    runs = [r for d in data for r in d.history]
    green = sum(1 for r in runs if not _failed(r))
    tests = [t for d in data for t in d.tests]
    flaky = sum(1 for t in tests if t.flaky)
    slower = sum(1 for t in tests if t.slowdown_ms)
    quarantined = sum(len(d.quarantined) for d in data)
    total_latest = executed + skipped
    unit = integration = e2e = 0
    for d in data:
        model = build_pyramid_model(d.run)
        unit, integration, e2e = unit + model.unit, integration + model.integration, e2e + model.e2e
    pyramid_total = unit + integration + e2e
    shape, shape_text = classify_shape(PyramidModel(unit=unit, integration=integration, e2e=e2e))

    return [
        HealthSignal(
            key="passing",
            name="Passing tests",
            score=_clamp(100 * passed / executed) if executed else None,
            weight=30,
            value=f"{passed} of {executed} tests passed" if executed else "No test results yet",
            how="Tests that passed in each cycle's latest run, out of those that ran.",
        ),
        HealthSignal(
            key="green_builds",
            name="Green builds",
            score=_clamp(100 * green / len(runs)) if runs else None,
            weight=20,
            value=f"{green} of {len(runs)} runs passed" if runs else "No runs yet",
            how=f"Runs that passed among each cycle's last {BUILD_WINDOW}.",
        ),
        HealthSignal(
            key="stable",
            name="Stable tests",
            score=_share_score(flaky, len(tests), _FLAKY_PENALTY),
            weight=20,
            value=f"{flaky} flaky of {len(tests)} tests" if tests else "No per-test results yet",
            how="Tests that passed and failed, flipping at least twice, in the last 20 runs. "
            "Each 1% of flaky tests costs 10 points.",
        ),
        HealthSignal(
            key="speed",
            name="Speed trend",
            score=_share_score(slower, len(tests), _SLOWER_PENALTY),
            weight=10,
            value=f"{slower} of {len(tests)} tests got slower" if tests else "No timings yet",
            how="Tests whose last 3 runs are slower than before, beyond noise. "
            "Each 1% of slower tests costs 5 points.",
        ),
        HealthSignal(
            key="hygiene",
            name="Test hygiene",
            score=_share_score(skipped + quarantined, total_latest, _HYGIENE_PENALTY),
            weight=10,
            value=f"{skipped} skipped, {quarantined} quarantined of {total_latest} tests"
            if total_latest
            else "No test results yet",
            how="Tests skipped or waived by a quarantine rule in the latest runs. "
            "Each 1% costs 3 points.",
        ),
        HealthSignal(
            key="pyramid",
            name="Test pyramid",
            score=_PYRAMID_SCORES[shape] if pyramid_total else None,
            weight=10,
            value=f"{unit} unit, {integration} integration, {e2e} e2e. {shape_text}"
            if pyramid_total
            else "No tiered tests yet",
            how="Mostly unit tests and fewest end-to-end tests scores best. "
            "Set `tier:` on each stage in qasight.yaml.",
        ),
    ]


def _duration(ms: int) -> str:
    """``850 ms``, ``13.9 s``: how the UI writes durations."""
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def _findings(data: list[_CycleData]) -> list[HealthFinding]:
    """Red cycles first, then flaky, slower, quarantined and skipped tests."""
    found: list[HealthFinding] = []
    for d in data:
        if _failed(d.run):
            failing = (d.run.failed or 0) + (d.run.broken or 0)
            found.append(
                HealthFinding(
                    kind="red_cycle",
                    cycle=d.run.cycle or "",
                    title=f"{d.run.cycle} is red",
                    detail=(
                        f"{failing} test{'s' if failing != 1 else ''} failing in the latest run."
                        if failing
                        else "No test failed: a stage failed outside its tests. Its log says why."
                    ),
                    run_id=d.run.run_id,
                )
            )
    flaky = [(d, t) for d in data for t in d.tests if t.flaky]
    flaky.sort(key=lambda dt: (-dt[1].failed, dt[1].name))
    found += [
        HealthFinding(
            kind="flaky",
            cycle=d.run.cycle or "",
            title=t.name,
            detail=f"Flaky: failed {t.failed} of {t.runs} runs.",
            test_key=t.key,
        )
        for d, t in flaky[:MAX_FINDINGS]
    ]
    slower = [(d, t) for d in data for t in d.tests if t.slowdown_ms]
    slower.sort(key=lambda dt: -(dt[1].slowdown_ms or 0))
    found += [
        HealthFinding(
            kind="slower",
            cycle=d.run.cycle or "",
            title=t.name,
            detail=f"Got slower by {_duration(t.slowdown_ms or 0)}"
            + (f" (+{round(t.slowdown_pct)}%)." if t.slowdown_pct is not None else "."),
            test_key=t.key,
        )
        for d, t in slower[:MAX_FINDINGS]
    ]
    for d in data:
        if d.quarantined:
            found.append(
                HealthFinding(
                    kind="quarantined",
                    cycle=d.run.cycle or "",
                    title=f"{len(d.quarantined)} quarantined in {d.run.cycle}",
                    detail="Failing but waived: "
                    + ", ".join(d.quarantined[:3])
                    + ("…" if len(d.quarantined) > 3 else ""),
                    run_id=d.run.run_id,
                )
            )
        if d.run.skipped:
            found.append(
                HealthFinding(
                    kind="skipped",
                    cycle=d.run.cycle or "",
                    title=f"{d.run.skipped} skipped in {d.run.cycle}",
                    detail="Skipped tests check nothing. Fix or delete them.",
                    run_id=d.run.run_id,
                )
            )
    return found


def _cycle_data(cycle: str | None) -> list[_CycleData]:
    latest = latest_run_per_cycle()
    if cycle is not None:
        latest = {cycle: latest[cycle]} if cycle in latest else {}
    return [
        _CycleData(
            run=run,
            history=list_cycle_runs(name, limit=BUILD_WINDOW),
            tests=test_history.list_cycle_tests(name).tests,
            quarantined=_quarantined(run),
        )
        for name, run in latest.items()
    ]


def project_health(*, cycle: str | None = None) -> ProjectHealth:
    """The score of every recorded cycle together, or of ``cycle`` alone."""
    data = _cycle_data(cycle)
    signals = _signals(data)
    score = _weighted(signals)
    by_cycle = []
    for d in data:
        tests = d.tests
        by_cycle.append(
            CycleHealth(
                cycle=d.run.cycle or "",
                run_id=d.run.run_id,
                created_at=d.run.created_at,
                passed_latest=not _failed(d.run),
                tests=d.run.total_tests or len(tests),
                failing=(d.run.failed or 0) + (d.run.broken or 0),
                flaky=sum(1 for t in tests if t.flaky),
                slower=sum(1 for t in tests if t.slowdown_ms),
                green_runs=sum(1 for r in d.history if not _failed(r)),
                runs=len(d.history),
                score=_weighted(_signals([d])),
            )
        )
    return ProjectHealth(
        scope=cycle,
        score=score,
        grade=grade_for(score),
        cycles=len(data),
        runs_considered=sum(len(d.history) for d in data),
        tests=sum(d.run.total_tests or len(d.tests) for d in data),
        latest_run_at=max((d.run.created_at for d in data), default=None),
        signals=tuple(signals),
        by_cycle=tuple(by_cycle),
        findings=tuple(_findings(data)),
    )
