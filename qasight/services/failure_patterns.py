"""Failure patterns: where failures concentrate, when they happen, and which tests earn their keep.

The small follow-ups to the report's bigger numbers, all over the period's runs:

* **Concentration.** How few tests account for most failures ("5 tests caused 62%
  of failures"), and the error messages that repeat most across tests (compared
  by :func:`~qasight.services.run_failures.message_signature`, so numbers and
  ids don't split them).
* **Retry rate.** Share of executed tests that passed only on a retry, per day.
  It climbs before tests get classified flaky.
* **Unstable timing.** Tests whose slowest runs (95th percentile) take several
  times their median: they are the next timeouts and flakes.
* **When failures happen.** Red runs by hour of day (UTC), weekday and
  environment, with a finding when one of them fails far more often than the rest.
* **Tests that catch bugs.** Per test, failures marked as a product bug (triage
  memory) against failures marked as noise (test bug, environment, flaky): the
  tests that find real problems, and the ones that only ever raise false alarms.
* **Coverage by day.** The last line coverage recorded each day.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from statistics import median
from typing import Any

from qasight.history.views import CompletedRunView
from qasight.services import flaky_service
from qasight.services.failure_signal import CauseLookup, triage_causes
from qasight.services.run_failures import message_signature

_PASS = "passed"
_FAIL = frozenset({"failed", "broken"})
_NOISE = frozenset({"test_bug", "environment", "flaky"})
MAX_ITEMS = 10
# A slice of runs counts as failing more often only with this many runs and this many times the rate.
MIN_SLICE_RUNS = 5
SLICE_FACTOR = 2.0
# Timing: p95 at least this many times the median, and at least this much slower in absolute terms.
SPREAD_FACTOR = 3.0
SPREAD_FLOOR_MS = 1000
MIN_TIMED_RUNS = 5
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class CountedTest:
    cycle: str
    name: str
    count: int
    detail: str = ""


@dataclass(frozen=True)
class RepeatedError:
    message: str  # one example of the message
    count: int  # failures with that signature
    tests: int  # different tests that hit it


@dataclass(frozen=True)
class Slice:
    label: str
    runs: int
    red: int


@dataclass(frozen=True)
class DayPoint:
    day: str
    retried: int
    executed: int
    retry_pct: float | None
    coverage_pct: float | None


@dataclass(frozen=True)
class Patterns:
    failures: int = 0
    # The fewest tests that together account for most of the failures, and their share.
    top_tests: list[CountedTest] = field(default_factory=list)
    top_share_pct: float | None = None
    repeated_errors: list[RepeatedError] = field(default_factory=list)
    retried: int = 0
    executed: int = 0
    retry_pct: float | None = None
    prev_retry_pct: float | None = None
    unstable_timing: list[CountedTest] = field(default_factory=list)
    by_hour: list[Slice] = field(default_factory=list)
    by_weekday: list[Slice] = field(default_factory=list)
    by_environment: list[Slice] = field(default_factory=list)
    # Plain-words findings from the slices: "Runs on Mondays failed 3x as often as the rest".
    when_findings: list[str] = field(default_factory=list)
    caught_bugs: list[CountedTest] = field(default_factory=list)
    only_noise: list[CountedTest] = field(default_factory=list)
    days: list[DayPoint] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(n: int, whole: int) -> float | None:
    return round(100.0 * n / whole, 1) if whole else None


def _p95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[max(0, -(-len(ordered) * 95 // 100) - 1)]


def _slice_findings(slices: list[Slice], name: str, total_runs: int, total_red: int) -> list[str]:
    out = []
    for s in slices:
        rest_runs, rest_red = total_runs - s.runs, total_red - s.red
        if s.runs < MIN_SLICE_RUNS or rest_runs < MIN_SLICE_RUNS or not s.red:
            continue
        rate, rest = s.red / s.runs, rest_red / rest_runs
        if rest == 0:
            out.append(f"Only runs {name} {s.label} failed ({s.red} of {s.runs} red).")
        elif rate >= SLICE_FACTOR * rest:
            out.append(
                f"Runs {name} {s.label} failed {rate / rest:.1f}x as often as the rest "
                f"({s.red} of {s.runs} red)."
            )
    return out


def _hour_label(hour: int) -> str:
    return f"{hour:02d}:00-{(hour + 1) % 24:02d}:00 UTC"


def build_patterns(
    runs: list[CompletedRunView],
    *,
    start: float,
    end: float,
    cycles: set[str] | None = None,
    causes: CauseLookup = triage_causes,
    days: list[str] | None = None,
) -> Patterns:
    """Failure patterns for [start, end]; ``days`` (YYYY-MM-DD, UTC) are the report's trend days."""
    prev_start = start - (end - start)
    kept = sorted(
        (
            r
            for r in runs
            if r.cycle and (not cycles or r.cycle in cycles) and prev_start <= r.created_at <= end
        ),
        key=lambda r: r.created_at,
    )
    now = [r for r in kept if r.created_at >= start]

    failures: Counter[tuple[str, str]] = Counter()
    names: dict[tuple[str, str], str] = {}
    errors: dict[str, tuple[str, set[tuple[str, str]], int]] = {}
    durations: dict[tuple[str, str], list[int]] = {}
    per_day: dict[str, list[int]] = {}  # day -> [retried, executed]
    coverage: dict[str, float] = {}
    retried_prev = executed_prev = 0
    marked_fail: dict[str, list[tuple[str, str]]] = {}  # cycle -> [(key, message)]
    hours: dict[int, list[int]] = {}
    weekdays: dict[int, list[int]] = {}
    envs: dict[str, list[int]] = {}
    for run in kept:
        cases = flaky_service._cases_for_run(run)
        in_period = run.created_at >= start
        red = run.returncode != 0
        when = datetime.fromtimestamp(run.created_at, tz=UTC)
        day = when.date().isoformat()
        if in_period:
            for bucket, k in ((hours, when.hour), (weekdays, when.weekday())):
                b = bucket.setdefault(k, [0, 0])
                b[0] += 1
                b[1] += red
            if run.environment:
                b = envs.setdefault(run.environment, [0, 0])
                b[0] += 1
                b[1] += red
            if run.coverage_pct is not None:
                coverage[day] = run.coverage_pct
        for key, case in cases.items():
            status = str(case.get("status"))
            executed = status == _PASS or status in _FAIL
            retried = bool(case.get("retried")) and status == _PASS
            if not in_period:
                executed_prev += executed
                retried_prev += retried
                continue
            cell = per_day.setdefault(day, [0, 0])
            cell[0] += retried
            cell[1] += executed
            ident = (str(run.cycle), key)
            names[ident] = str(case.get("name") or key)
            ms = case.get("duration_ms")
            if status == _PASS and isinstance(ms, int):
                durations.setdefault(ident, []).append(ms)
            if status in _FAIL:
                failures[ident] += 1
                message = str(case.get("message") or "")
                if message:
                    sig = message_signature(message)[:200]
                    example, tests, count = errors.get(sig, (message, set(), 0))
                    tests.add(ident)
                    errors[sig] = (example, tests, count + 1)
                    marked_fail.setdefault(str(run.cycle), []).append((key, message))

    total = sum(failures.values())
    ranked = failures.most_common()
    top: list[CountedTest] = []
    covered = 0
    for (cycle, key), count in ranked:
        if covered >= 0.6 * total or len(top) >= MAX_ITEMS:
            break
        top.append(CountedTest(cycle, names[(cycle, key)], count))
        covered += count
    repeated = sorted(
        (
            RepeatedError(example[:300], count, len(tests))
            for example, tests, count in errors.values()
        ),
        key=lambda e: (-e.count, -e.tests, e.message),
    )
    repeated = [e for e in repeated if e.count > 1][:MAX_ITEMS]

    unstable = []
    for ident, values in durations.items():
        if len(values) < MIN_TIMED_RUNS:
            continue
        mid, p95 = int(median(values)), _p95(values)
        if p95 >= SPREAD_FACTOR * max(mid, 1) and p95 - mid >= SPREAD_FLOOR_MS:
            unstable.append(
                CountedTest(
                    ident[0],
                    names[ident],
                    p95 - mid,
                    f"median {mid / 1000:.1f}s, slowest runs {p95 / 1000:.1f}s",
                )
            )
    unstable.sort(key=lambda t: (-t.count, t.cycle, t.name))

    caught: Counter[tuple[str, str]] = Counter()
    noise: Counter[tuple[str, str]] = Counter()
    for cycle, pairs in marked_fail.items():
        marked = causes(cycle, {m for _, m in pairs})
        for key, message in pairs:
            cause = marked.get(message)
            if cause == "product_bug":
                caught[(cycle, key)] += 1
            elif cause in _NOISE:
                noise[(cycle, key)] += 1

    by_hour = [Slice(_hour_label(h), *hours[h]) for h in sorted(hours)]
    by_weekday = [Slice(WEEKDAYS[d], *weekdays[d]) for d in sorted(weekdays)]
    by_env = sorted((Slice(e, *v) for e, v in envs.items()), key=lambda s: (-s.runs, s.label))
    total_runs = len(now)
    total_red = sum(1 for r in now if r.returncode != 0)
    findings = (
        _slice_findings(by_weekday, "on", total_runs, total_red)
        + _slice_findings(by_hour, "at", total_runs, total_red)
        + _slice_findings(by_env, "on", total_runs, total_red)
    )
    retried_now = sum(c[0] for c in per_day.values())
    executed_now = sum(c[1] for c in per_day.values())
    day_points = [
        DayPoint(
            day=d,
            retried=per_day.get(d, [0, 0])[0],
            executed=per_day.get(d, [0, 0])[1],
            retry_pct=_pct(*per_day.get(d, [0, 0])),
            coverage_pct=coverage.get(d),
        )
        for d in (days or sorted(per_day))
    ]
    return Patterns(
        failures=total,
        top_tests=top,
        top_share_pct=_pct(covered, total),
        repeated_errors=repeated,
        retried=retried_now,
        executed=executed_now,
        retry_pct=_pct(retried_now, executed_now),
        prev_retry_pct=_pct(retried_prev, executed_prev),
        unstable_timing=unstable[:MAX_ITEMS],
        by_hour=by_hour,
        by_weekday=by_weekday,
        by_environment=by_env,
        when_findings=findings[:5],
        caught_bugs=[
            CountedTest(c, names.get((c, k), k), n, f"{noise[(c, k)]} false alarms")
            for (c, k), n in caught.most_common(MAX_ITEMS)
        ],
        only_noise=[
            CountedTest(c, names.get((c, k), k), n)
            for (c, k), n in noise.most_common()
            if (c, k) not in caught
        ][:MAX_ITEMS],
        days=day_points,
    )
