"""One test across a cycle's recent runs: its outcomes, durations and last failure.

Feeds the per-test page and test search (``GET /api/v1/cycles/{cycle}/tests``
and ``.../test-history``), and the cycle page's "Slowest tests" and "Got slower"
lists. Like :mod:`~qasight.services.flaky_service`, per-test data comes from
each run's own Allure ``*-result.json`` snapshot, so it needs no schema change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from qasight.history.views import CompletedRunView
from qasight.services import flaky_service
from qasight.services.delta_service import within_noise

DEFAULT_WINDOW = 20
# "Got slower" compares the median of the last RECENT_RUNS durations with the median before.
RECENT_RUNS = 3
# A single test's noise floor: below this a change is jitter, whatever its percentage.
SLOWDOWN_FLOOR_MS = 20.0
# Runs in the rolling window a point's flip rate is measured over.
FLIP_WINDOW = 5

_FAIL = frozenset({"failed", "broken"})


@dataclass(frozen=True)
class TestPoint:
    """The test in one run."""

    __test__ = False  # not a pytest test class

    run_id: str
    created_at: float
    status: str
    duration_ms: int | None
    message: str
    branch: str | None
    # Share of outcome changes (pass <-> fail) over the FLIP_WINDOW runs ending here;
    # None until two of them passed or failed.
    flip_rate: float | None = None
    # What the run tested on ("browser=firefox"), when it recorded one.
    environment: str | None = None


@dataclass(frozen=True)
class EnvironmentOutcome:
    """The test's results on one environment, across the window."""

    environment: str
    runs: int
    passed: int
    failed: int
    last_status: str


@dataclass(frozen=True)
class TestSummary:
    """One line of the cycle's test list."""

    __test__ = False  # not a pytest test class

    key: str
    name: str
    full_name: str
    group: str
    stage: str
    runs: int
    passed: int
    failed: int
    last_status: str
    flaky: bool
    last_duration_ms: int | None
    median_duration_ms: int | None
    # Recent median minus the median before; set only when that is more than noise.
    slowdown_ms: int | None
    slowdown_pct: float | None
    # The slowest runs (95th percentile of passing and failing runs alike); far above the
    # median means unstable timing, the next timeout or flake.
    p95_duration_ms: int | None = None
    # From the latest run: what the test is about ({name, value}) and its issue links.
    labels: tuple[dict[str, str], ...] = ()
    links: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class CycleTests:
    cycle: str
    runs_considered: int
    tests: tuple[TestSummary, ...]


@dataclass(frozen=True)
class TestHistory:
    __test__ = False  # not a pytest test class

    cycle: str
    summary: TestSummary
    # Newest first.
    points: tuple[TestPoint, ...]
    # One line per environment the runs recorded, most failures first; empty when none did.
    environments: tuple[EnvironmentOutcome, ...] = ()

    @property
    def last_failure(self) -> TestPoint | None:
        return next((p for p in self.points if p.status in _FAIL), None)


def stable_id(key: str) -> str:
    """FNV-1a (32-bit) of ``key`` as 8 hex digits: a file name for the static demo export.

    ``frontend/src/lib/static-backend.ts`` computes the same hash to find the file.
    """
    h = 0x811C9DC5
    for byte in key.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def _observations(
    cycle: str, window: int
) -> tuple[int, dict[str, list[tuple[CompletedRunView, dict[str, Any]]]]]:
    """Runs analysed, and each test's ``(run, case)`` pairs oldest first."""
    runs = flaky_service._cycle_runs(cycle)[: max(1, int(window))]
    per_test: dict[str, list[tuple[CompletedRunView, dict[str, Any]]]] = {}
    analysed = 0
    for run in reversed(runs):
        cases = flaky_service._cases_for_run(run)
        if not cases:
            continue
        analysed += 1
        for key, case in cases.items():
            per_test.setdefault(key, []).append((run, case))
    return analysed, per_test


def _slowdown(durations: list[int]) -> tuple[int | None, float | None]:
    """Change of the recent median over the earlier one, when it is more than noise."""
    if len(durations) < RECENT_RUNS + 2:
        return None, None
    before = median(durations[:-RECENT_RUNS])
    recent = median(durations[-RECENT_RUNS:])
    delta = recent - before
    if delta <= 0 or within_noise(
        absolute_delta=delta, baseline=before, floor_ms=SLOWDOWN_FLOOR_MS
    ):
        return None, None
    return int(delta), (round(100.0 * delta / before, 1) if before else None)


def _summarise(key: str, observed: list[tuple[CompletedRunView, dict[str, Any]]]) -> TestSummary:
    cases = [flaky_service.observation(run, case) for run, case in observed]
    latest = cases[-1]
    statuses = [str(c["status"]) for c in cases]
    durations = [int(c["duration_ms"]) for c in cases if isinstance(c.get("duration_ms"), int)]
    slowdown_ms, slowdown_pct = _slowdown(durations)
    return TestSummary(
        key=key,
        name=str(latest.get("name") or key),
        full_name=str(latest.get("full_name") or ""),
        group=str(latest.get("group") or "(ungrouped)"),
        stage=str(latest.get("stage") or ""),
        runs=len(cases),
        passed=statuses.count("passed"),
        failed=sum(1 for s in statuses if s in _FAIL),
        last_status=statuses[-1],
        flaky=flaky_service.classify(key, cases) is not None,
        last_duration_ms=latest.get("duration_ms")
        if isinstance(latest.get("duration_ms"), int)
        else None,
        median_duration_ms=int(median(durations)) if durations else None,
        slowdown_ms=slowdown_ms,
        slowdown_pct=slowdown_pct,
        p95_duration_ms=sorted(durations)[max(0, -(-len(durations) * 95 // 100) - 1)]
        if durations
        else None,
        labels=tuple(latest.get("labels") or ()),
        links=tuple(latest.get("links") or ()),
    )


def _flip_rates(statuses: list[str]) -> list[float | None]:
    """Rolling flip rate per run (oldest first): how flaky the test was up to that run."""
    rates: list[float | None] = []
    for i in range(len(statuses)):
        outcomes = flaky_service._outcomes(statuses[max(0, i - FLIP_WINDOW + 1) : i + 1])
        if len(outcomes) < 2:
            rates.append(None)
            continue
        flips = sum(1 for a, b in zip(outcomes, outcomes[1:], strict=False) if a != b)
        rates.append(round(flips / (len(outcomes) - 1), 3))
    return rates


def _matches(summary: TestSummary, query: str) -> bool:
    needle = query.casefold()
    fields = (
        summary.name,
        summary.full_name,
        summary.group,
        *(lab.get("value", "") for lab in summary.labels),
    )
    return any(needle in field.casefold() for field in fields)


def has_label(labels: tuple[dict[str, str], ...] | list[dict[str, str]], wanted: str) -> bool:
    """Whether ``labels`` holds ``wanted``: ``name:value`` exactly, or a bare value under any name.

    Case-insensitive, so ``feature:checkout`` matches the label ``feature=Checkout``
    and ``smoke`` matches the tag ``smoke``.
    """
    wanted = wanted.strip().casefold()
    if not wanted:
        return True
    name, sep, value = wanted.partition(":")
    for lab in labels:
        lab_name = str(lab.get("name", "")).casefold()
        lab_value = str(lab.get("value", "")).casefold()
        if sep and lab_name == name.strip() and lab_value == value.strip():
            return True
        if lab_value == wanted:
            return True
    return False


def list_cycle_tests(
    cycle: str,
    *,
    window: int = DEFAULT_WINDOW,
    query: str | None = None,
    label: str | None = None,
) -> CycleTests:
    """Every test seen in ``cycle``'s last ``window`` runs, failing first, then by name.

    ``query`` matches name, full name, group or a label's value; ``label`` keeps
    only the tests that carry that label (see :func:`has_label`).
    """
    analysed, per_test = _observations(cycle, window)
    tests = [_summarise(key, observed) for key, observed in per_test.items()]
    if query and query.strip():
        tests = [t for t in tests if _matches(t, query.strip())]
    if label and label.strip():
        tests = [t for t in tests if has_label(t.labels, label)]
    tests.sort(key=lambda t: (t.last_status not in _FAIL, not t.flaky, t.name, t.key))
    return CycleTests(cycle=cycle, runs_considered=analysed, tests=tuple(tests))


def load_test_history(cycle: str, key: str, *, window: int = DEFAULT_WINDOW) -> TestHistory | None:
    """``key``'s outcome in each of ``cycle``'s last ``window`` runs; ``None`` if it never ran."""
    _, per_test = _observations(cycle, window)
    observed = per_test.get(key)
    if not observed:
        return None
    rates = _flip_rates([str(case["status"]) for _, case in observed])
    points = tuple(
        TestPoint(
            run_id=run.run_id,
            created_at=run.created_at,
            status=str(case["status"]),
            duration_ms=case.get("duration_ms")
            if isinstance(case.get("duration_ms"), int)
            else None,
            message=str(case.get("message") or ""),
            branch=run.branch,
            flip_rate=rate,
            environment=run.environment,
        )
        for (run, case), rate in reversed(list(zip(observed, rates, strict=True)))
    )
    return TestHistory(
        cycle=cycle,
        summary=_summarise(key, observed),
        points=points,
        environments=_by_environment(points),
    )


def _by_environment(points: tuple[TestPoint, ...]) -> tuple[EnvironmentOutcome, ...]:
    """Pass and fail counts per environment (``points`` newest first); ``()`` if none recorded one."""
    if not any(p.environment for p in points):
        return ()
    groups: dict[str, list[TestPoint]] = {}
    for p in points:
        groups.setdefault(p.environment or "(not set)", []).append(p)
    out = [
        EnvironmentOutcome(
            environment=env,
            runs=len(members),
            passed=sum(1 for p in members if p.status == "passed"),
            failed=sum(1 for p in members if p.status in _FAIL),
            last_status=members[0].status,
        )
        for env, members in groups.items()
    ]
    out.sort(key=lambda e: (-e.failed, e.environment))
    return tuple(out)


def cycle_source(cycle: str) -> tuple[Path | None, str | None]:
    """The checkout and ``owner/name`` repository of the cycle's latest run that recorded them."""
    for run in flaky_service._cycle_runs(cycle):
        if run.target_repo or run.repository:
            return (Path(run.target_repo) if run.target_repo else None), run.repository
    return None, None


def test_presence(cycle: str, *, window: int = DEFAULT_WINDOW) -> dict[str, tuple[str, int, int]]:
    """``key -> (name, first_seen, last_seen)`` in runs ago (0 = the latest run analysed).

    A test missing from the latest runs (``last_seen > 0``) was removed or renamed; one
    with a small ``first_seen`` is new. Feeds the AI review's test churn check.
    """
    runs = [r.run_id for r in flaky_service._cycle_runs(cycle)[: max(1, int(window))]]
    _, per_test = _observations(cycle, window)
    analysed = [
        r for r in runs if any(r == run.run_id for obs in per_test.values() for run, _ in obs)
    ]
    position = {run_id: index for index, run_id in enumerate(analysed)}
    presence: dict[str, tuple[str, int, int]] = {}
    for key, observed in per_test.items():
        ago = [position[run.run_id] for run, _ in observed if run.run_id in position]
        if ago:
            presence[key] = (str(observed[-1][1].get("name") or key), max(ago), min(ago))
    return presence
