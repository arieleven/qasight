"""Releases: the runs that tested one release ("2.3"), for the Releases page.

Runs carry a release when they were recorded with ``--release`` or
``TESTO_RELEASE`` (:mod:`~qasight.services.run_release`). A release's state is
the latest run of each cycle that tested it: its pass rate, its failing tests,
and the tests that were flaky across the release's runs. Each release is
compared with the one before it (by when it was first tested): which failures
are new and which tests were fixed. Per-test outcomes come from the same
``test_result`` rows as the rest of the analytics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qasight.history.cases import cases_for_run
from qasight.history.read_model import latest_run_per_cycle, list_cycle_runs
from qasight.history.views import CompletedRunView
from qasight.services import flaky_service

_FAIL = frozenset({"failed", "broken"})
# Failing tests listed per release; the counts cover all of them.
MAX_TESTS = 200


@dataclass(frozen=True)
class ReleaseSummary:
    release: str
    first_run_at: float
    last_run_at: float
    runs: int
    cycles: int
    # Totals over the latest run of each cycle in the release.
    total: int
    passed: int
    failed: int
    skipped: int

    @property
    def pass_rate(self) -> float | None:
        ran = self.total - self.skipped
        return round(100.0 * self.passed / ran, 1) if ran > 0 else None


@dataclass(frozen=True)
class ReleaseCycle:
    """One cycle's latest run in the release."""

    cycle: str
    run_id: str
    created_at: float
    runs: int
    total: int
    passed: int
    failed: int
    skipped: int
    flaky: int


@dataclass(frozen=True)
class ReleaseTest:
    cycle: str
    key: str
    name: str
    status: str
    message: str
    run_id: str
    # Passed (or did not run) in the previous release's latest run of this cycle.
    new: bool = False


@dataclass(frozen=True)
class ReleaseDetail:
    summary: ReleaseSummary
    cycles: tuple[ReleaseCycle, ...]
    failing: tuple[ReleaseTest, ...]
    # Failed in the previous release, passed in this one.
    fixed: tuple[ReleaseTest, ...]
    flaky: int
    previous: ReleaseSummary | None


def _runs_by_release() -> dict[str, dict[str, list[CompletedRunView]]]:
    """``release -> cycle -> runs`` (newest first), over every cycle's stored history."""
    out: dict[str, dict[str, list[CompletedRunView]]] = {}
    for cycle in latest_run_per_cycle():
        for run in list_cycle_runs(cycle):
            if run.release:
                out.setdefault(run.release, {}).setdefault(cycle, []).append(run)
    return out


def _summary(release: str, by_cycle: dict[str, list[CompletedRunView]]) -> ReleaseSummary:
    runs = [r for rs in by_cycle.values() for r in rs]
    latest = [rs[0] for rs in by_cycle.values()]
    return ReleaseSummary(
        release=release,
        first_run_at=min(r.created_at for r in runs),
        last_run_at=max(r.created_at for r in runs),
        runs=len(runs),
        cycles=len(by_cycle),
        total=sum(r.total_tests or 0 for r in latest),
        passed=sum(r.passed or 0 for r in latest),
        failed=sum((r.failed or 0) + (r.broken or 0) for r in latest),
        skipped=sum(r.skipped or 0 for r in latest),
    )


def list_releases() -> tuple[ReleaseSummary, ...]:
    """Every release a run recorded, the most recently first-tested first."""
    summaries = [_summary(name, cycles) for name, cycles in _runs_by_release().items()]
    summaries.sort(key=lambda s: (s.first_run_at, s.release), reverse=True)
    return tuple(summaries)


def _flaky_keys(runs: list[CompletedRunView]) -> set[str]:
    """Tests that both passed and failed (or passed on retry) across ``runs`` (newest first)."""
    return flaky_service.flaky_keys((run, cases_for_run(run)) for run in reversed(runs))


def _test(
    cycle: str, run: CompletedRunView, key: str, case: dict[str, Any], **kw: Any
) -> ReleaseTest:
    return ReleaseTest(
        cycle=cycle,
        key=key,
        name=str(case.get("name") or key),
        status=str(case.get("status") or ""),
        message=str(case.get("message") or "").strip().splitlines()[0][:300]
        if case.get("message")
        else "",
        run_id=run.run_id,
        **kw,
    )


def load_release(release: str) -> ReleaseDetail | None:
    """``release``'s state and its change from the release before; ``None`` if no run tested it."""
    grouped = _runs_by_release()
    by_cycle = grouped.get(release)
    if not by_cycle:
        return None
    summary = _summary(release, by_cycle)
    earlier = sorted(
        (_summary(name, cycles) for name, cycles in grouped.items() if name != release),
        key=lambda s: (s.first_run_at, s.release),
    )
    previous = next(
        (
            s
            for s in reversed(earlier)
            if (s.first_run_at, s.release) < (summary.first_run_at, release)
        ),
        None,
    )
    prev_cycles = grouped.get(previous.release, {}) if previous else {}

    cycles: list[ReleaseCycle] = []
    failing: list[ReleaseTest] = []
    fixed: list[ReleaseTest] = []
    flaky_total = 0
    for cycle in sorted(by_cycle):
        runs = by_cycle[cycle]
        latest = runs[0]
        cases = cases_for_run(latest)
        flaky = _flaky_keys(runs)
        flaky_total += len(flaky)
        prev_run = prev_cycles[cycle][0] if cycle in prev_cycles else None
        prev_cases = cases_for_run(prev_run) if prev_run else {}
        for key, case in cases.items():
            if str(case.get("status")) in _FAIL:
                was = str(prev_cases.get(key, {}).get("status") or "")
                failing.append(
                    _test(cycle, latest, key, case, new=bool(prev_run) and was not in _FAIL)
                )
        for key, case in prev_cases.items():
            if (
                str(case.get("status")) in _FAIL
                and str(cases.get(key, {}).get("status")) == "passed"
            ):
                fixed.append(_test(cycle, latest, key, cases[key]))
        cycles.append(
            ReleaseCycle(
                cycle=cycle,
                run_id=latest.run_id,
                created_at=latest.created_at,
                runs=len(runs),
                total=latest.total_tests or 0,
                passed=latest.passed or 0,
                failed=(latest.failed or 0) + (latest.broken or 0),
                skipped=latest.skipped or 0,
                flaky=len(flaky),
            )
        )
    failing.sort(key=lambda t: (not t.new, t.cycle, t.name))
    fixed.sort(key=lambda t: (t.cycle, t.name))
    return ReleaseDetail(
        summary=summary,
        cycles=tuple(cycles),
        failing=tuple(failing[:MAX_TESTS]),
        fixed=tuple(fixed[:MAX_TESTS]),
        flaky=flaky_total,
        previous=previous,
    )
