"""Failure signal: how many red runs were real bugs, and what flaky tests cost.

Two questions behind "can we trust a red build?":

* **Real failures vs noise.** Every red run in the period gets one cause, from what
  made it red: the causes people marked on its failures (triage memory,
  ``services/triage.py``), the flaky classification
  (:mod:`~qasight.services.flaky_service`), or nothing at all. The order
  decides when a run has several: a product bug wins over a test bug, then
  environment, other, failures nobody has marked yet, and flaky last, so a run
  counts as flaky only when every failure in it was a flaky test. A red run with
  no failing test (a crash, a setup error, a timeout) is its own cause.
* **Cost of flakiness.** For each flaky test, the CI time of its retries plus its
  share of the red runs that only flaky tests caused (someone reran those jobs),
  how many such runs, and which branches they held up
  (:func:`~qasight.services.flaky_service.flaky_costs`).
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from qasight.history.views import CompletedRunView
from qasight.services import flaky_service

logger = logging.getLogger(__name__)

_FAIL = frozenset({"failed", "broken"})
MAX_COSTLY = 10

# Most decisive first: a run takes the first of these that any of its failures has.
CAUSE_ORDER: tuple[tuple[str, str], ...] = (
    ("product_bug", "Real bug"),
    ("test_bug", "Bug in the test"),
    ("environment", "Environment or infrastructure"),
    ("other", "Other"),
    ("unmarked", "Not marked yet"),
    ("flaky", "Flaky tests"),
    ("no_tests", "Crash or setup (no test failed)"),
)
_RANK = {cause: i for i, (cause, _) in enumerate(CAUSE_ORDER)}
# Causes that mean the red build said nothing about the product.
NOISE = frozenset({"test_bug", "environment", "flaky", "no_tests"})

# (cycle, messages) -> {message: cause}
CauseLookup = Callable[[str, set[str]], dict[str, str]]


@dataclass(frozen=True)
class CauseCount:
    cause: str
    label: str
    runs: int


@dataclass(frozen=True)
class CostlyFlakyTest:
    cycle: str
    key: str
    name: str
    cost_minutes: float
    retries: int
    red_runs: int
    branches: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Signal:
    red_runs: int = 0
    prev_red_runs: int = 0
    by_cause: list[CauseCount] = field(default_factory=list)
    # Shares of the red runs: real bugs, noise (test bugs, environment, flaky, crashes)
    # and not marked yet. None when there were no red runs.
    real_pct: float | None = None
    noise_pct: float | None = None
    unmarked_pct: float | None = None
    prev_real_pct: float | None = None
    prev_noise_pct: float | None = None
    flaky_minutes: float = 0.0
    prev_flaky_minutes: float = 0.0
    flaky_red_runs: int = 0
    # Branches other than the default one held up by a red run only flaky tests caused.
    flaky_branches: int = 0
    costly: list[CostlyFlakyTest] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def triage_causes(cycle: str, messages: set[str]) -> dict[str, str]:
    """The marked cause of each message, from triage memory; ``{}`` if the store can't be read."""
    from qasight.services.triage import TriageStore

    try:
        return {m: t.cause for m, t in TriageStore().for_messages(cycle, messages).items()}
    except Exception:  # noqa: BLE001 - the report must not fail on a missing table
        logger.warning("could not read failure causes for %s", cycle, exc_info=True)
        return {}


def run_cause(cases: dict[str, dict[str, Any]], flaky: set[str], causes: dict[str, str]) -> str:
    """The one cause of a red run (see :data:`CAUSE_ORDER`)."""
    found: list[str] = []
    for key, case in cases.items():
        if str(case.get("status")) not in _FAIL:
            continue
        marked = causes.get(str(case.get("message") or ""))
        if marked in _RANK:
            found.append(str(marked))
        elif key in flaky:
            found.append("flaky")
        else:
            found.append("unmarked")
    return min(found, key=_RANK.__getitem__) if found else "no_tests"


def _pct(n: int, whole: int) -> float | None:
    return round(100.0 * n / whole, 1) if whole else None


@dataclass
class _Tally:
    causes: Counter[str] = field(default_factory=Counter)
    flaky_ms: float = 0.0
    flaky_red: int = 0
    branches: set[str] = field(default_factory=set)

    @property
    def red(self) -> int:
        return sum(self.causes.values())

    def share(self, which: Iterable[str]) -> float | None:
        return _pct(sum(self.causes[c] for c in which), self.red)


def build_signal(
    runs: list[CompletedRunView],
    *,
    start: float,
    end: float,
    cycles: set[str] | None = None,
    default_branch: str | None = None,
    causes: CauseLookup = triage_causes,
) -> Signal:
    """Causes of the red runs in [start, end] and the cost of flaky tests, against the period before.

    Flakiness is judged over both periods together, so a test that flipped last
    week still counts as flaky when it fails this week.
    """
    prev_start = start - (end - start)
    by_cycle: dict[str, list[CompletedRunView]] = {}
    for run in sorted(runs, key=lambda r: r.created_at):
        if (
            run.cycle
            and (not cycles or run.cycle in cycles)
            and prev_start <= run.created_at <= end
        ):
            by_cycle.setdefault(run.cycle, []).append(run)

    now, prev = _Tally(), _Tally()
    costly: list[CostlyFlakyTest] = []
    for cycle, cycle_runs in by_cycle.items():
        with_cases = [(r, flaky_service._cases_for_run(r)) for r in cycle_runs]
        names = {
            key: str(case.get("name") or key)
            for _run, cases in with_cases
            for key, case in cases.items()
        }
        flaky = flaky_service.flaky_keys(with_cases)
        messages = {
            str(c.get("message"))
            for run, cases in with_cases
            if run.returncode != 0
            for c in cases.values()
            if str(c.get("status")) in _FAIL and c.get("message")
        }
        marked = causes(cycle, messages) if messages else {}
        other_cause: set[str] = set()
        for run, cases in with_cases:
            tally = now if run.created_at >= start else prev
            if run.returncode != 0:
                cause = run_cause(cases, flaky, marked)
                tally.causes[cause] += 1
                if cause != "flaky":
                    other_cause.add(run.run_id)
                elif flaky_service.flaky_red(run, cases, flaky):
                    tally.flaky_red += 1
                    if run.branch and run.branch != default_branch:
                        tally.branches.add(f"{cycle}:{run.branch}")
        for label, tally, pairs in (
            ("now", now, [(r, c) for r, c in with_cases if r.created_at >= start]),
            ("prev", prev, [(r, c) for r, c in with_cases if r.created_at < start]),
        ):
            costs = flaky_service.flaky_costs(pairs, flaky, not_flaky_runs=frozenset(other_cause))
            for key, cost in costs.items():
                tally.flaky_ms += cost.retry_ms + cost.red_run_ms
                if label == "now" and cost.minutes > 0:
                    costly.append(
                        CostlyFlakyTest(
                            cycle=cycle,
                            key=key,
                            name=names.get(key, key),
                            cost_minutes=cost.minutes,
                            retries=cost.retries,
                            red_runs=cost.red_runs,
                            branches=list(cost.branches),
                        )
                    )
    costly.sort(key=lambda c: (-c.cost_minutes, -c.red_runs, c.cycle, c.name))
    return Signal(
        red_runs=now.red,
        prev_red_runs=prev.red,
        by_cause=[
            CauseCount(cause, label, now.causes[cause])
            for cause, label in CAUSE_ORDER
            if now.causes[cause]
        ],
        real_pct=now.share(["product_bug"]),
        noise_pct=now.share(NOISE),
        unmarked_pct=now.share(["unmarked"]),
        prev_real_pct=prev.share(["product_bug"]),
        prev_noise_pct=prev.share(NOISE),
        flaky_minutes=round(now.flaky_ms / 60000, 1),
        prev_flaky_minutes=round(prev.flaky_ms / 60000, 1),
        flaky_red_runs=now.flaky_red,
        flaky_branches=len(now.branches),
        costly=costly[:MAX_COSTLY],
    )
