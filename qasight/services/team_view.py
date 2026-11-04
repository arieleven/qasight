"""Stability per team: each owner's tests, failures, flaky tests and time to fix.

One shared health score has no owner, so nobody acts on it. Splitting the same
numbers by who owns the tests gives every team its own line. A test's owners are
its CODEOWNERS owners (when the cycle has a checkout with a CODEOWNERS file,
:mod:`~qasight.services.test_ownership`) plus any ``owner`` label it carries
(Allure ``owner``, a pytest marker, a JUnit property). Tests with neither are
counted under "(no owner)". A test with two owners counts for both.

Per owner, for the period: tests in the latest run, how many fail there now, flaky
tests and the CI minutes they cost, failing tests fixed and their median time to
fix, and failures still open with the oldest one's age. Time to fix and open
failures use the default branch, as in :mod:`~qasight.services.delivery_metrics`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from qasight.history.views import CompletedRunView
from qasight.services import flaky_service
from qasight.services.delivery_metrics import (
    DAY,
    LOOKBACK_DAYS,
    MAX_CASE_RUNS,
    default_branch,
    on_default_branch,
    red_spells,
)

NO_OWNER = "(no owner)"
MAX_OWNERS = 25
_FAIL = frozenset({"failed", "broken"})

# (cycle, case) -> owners of that test
OwnerResolver = Callable[[str, dict[str, Any]], tuple[str, ...]]


@dataclass(frozen=True)
class OwnerRow:
    owner: str
    tests: int = 0
    failing: int = 0
    flaky: int = 0
    flaky_minutes: float = 0.0
    fixes: int = 0
    median_fix_s: float | None = None
    open_failures: int = 0
    oldest_open_s: float | None = None


@dataclass(frozen=True)
class Teams:
    # False when no test had an owner: every row is "(no owner)", and the page says how to add owners.
    owners_known: bool = False
    rows: list[OwnerRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def label_owners(case: dict[str, Any]) -> tuple[str, ...]:
    """The values of the test's ``owner`` labels."""
    return tuple(
        str(label.get("value"))
        for label in case.get("labels") or []
        if isinstance(label, dict) and label.get("name") == "owner" and label.get("value")
    )


def default_owners() -> OwnerResolver:
    """CODEOWNERS owners from each cycle's checkout, plus ``owner`` labels."""
    from qasight.services.test_history import cycle_source
    from qasight.services.test_ownership import owners_for

    checkouts: dict[str, Path | None] = {}

    def resolve(cycle: str, case: dict[str, Any]) -> tuple[str, ...]:
        if cycle not in checkouts:
            checkouts[cycle] = cycle_source(cycle)[0]
        found = owners_for(
            checkouts[cycle], str(case.get("full_name") or ""), str(case.get("group") or "")
        )
        return tuple(dict.fromkeys((*found, *label_owners(case))))

    return resolve


@dataclass
class _Acc:
    tests: int = 0
    failing: int = 0
    flaky: int = 0
    flaky_ms: float = 0.0
    fixes: list[float] = field(default_factory=list)
    open_ages: list[float] = field(default_factory=list)


def build_teams(
    runs: list[CompletedRunView],
    *,
    start: float,
    end: float,
    cycles: set[str] | None = None,
    owners: OwnerResolver | None = None,
) -> Teams:
    """Each owner's numbers for [start, end] (see the module docstring), most open failures first."""
    resolve = owners or default_owners()
    prev_start = start - (end - start)
    oldest = prev_start - LOOKBACK_DAYS * DAY
    kept = sorted(
        (
            r
            for r in runs
            if r.cycle and (not cycles or r.cycle in cycles) and oldest <= r.created_at <= end
        ),
        key=lambda r: r.created_at,
    )
    any_branch = any(r.branch for r in kept)
    branch = default_branch(kept)
    by_cycle: dict[str, list[CompletedRunView]] = {}
    for run in kept:
        by_cycle.setdefault(str(run.cycle), []).append(run)

    acc: dict[str, _Acc] = {}
    known = False
    for cycle, cycle_runs in by_cycle.items():
        recent = [r for r in cycle_runs if prev_start <= r.created_at <= end]
        if not any(r.created_at >= start for r in recent):
            continue
        with_cases = [(r, c) for r in recent if (c := flaky_service._cases_for_run(r))]
        if not with_cases:
            continue
        observations: dict[str, list[dict[str, Any]]] = {}
        every_case: dict[str, dict[str, Any]] = {}
        for run, cases in with_cases:
            for key, case in cases.items():
                observations.setdefault(key, []).append(flaky_service.observation(run, case))
                every_case[key] = case
        flaky = {k for k, obs in observations.items() if flaky_service.classify(k, obs)}
        costs = flaky_service.flaky_costs(
            [(r, c) for r, c in with_cases if r.created_at >= start], flaky
        )
        on_default = [r for r in cycle_runs if on_default_branch(r, branch, any_branch)]
        closed, still = red_spells(on_default[-MAX_CASE_RUNS:], cycle)

        resolved = {key: resolve(cycle, case) for key, case in every_case.items()}
        known = known or any(resolved.values())

        def owners_of(key: str, resolved: dict[str, tuple[str, ...]] = resolved) -> tuple[str, ...]:
            return resolved.get(key) or (NO_OWNER,)

        latest = with_cases[-1][1]
        for key, case in latest.items():
            for owner in owners_of(key):
                a = acc.setdefault(owner, _Acc())
                a.tests += 1
                a.failing += str(case.get("status")) in _FAIL
        for key in flaky:
            for owner in owners_of(key):
                a = acc.setdefault(owner, _Acc())
                a.flaky += 1
                if key in costs:
                    a.flaky_ms += costs[key].retry_ms + costs[key].red_run_ms
        for key, at, secs in closed:
            if start <= at <= end:
                for owner in owners_of(key):
                    acc.setdefault(owner, _Acc()).fixes.append(secs)
        for f in still:
            for owner in owners_of(f.key):
                acc.setdefault(owner, _Acc()).open_ages.append(f.age_s)

    rows = [
        OwnerRow(
            owner=owner,
            tests=a.tests,
            failing=a.failing,
            flaky=a.flaky,
            flaky_minutes=round(a.flaky_ms / 60000, 1),
            fixes=len(a.fixes),
            median_fix_s=median(a.fixes) if a.fixes else None,
            open_failures=len(a.open_ages),
            oldest_open_s=max(a.open_ages) if a.open_ages else None,
        )
        for owner, a in acc.items()
    ]
    rows.sort(key=lambda r: (r.owner == NO_OWNER, -r.open_failures, -r.failing, -r.flaky, r.owner))
    return Teams(owners_known=known, rows=rows[:MAX_OWNERS])
