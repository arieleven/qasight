"""What needs someone now, across every cycle: the dashboard's first card.

For each cycle's latest run: is it red (and since when, with how many new
failures), which tests it made flaky, and which tests got slower. Built from
the same services as the run page (:mod:`~qasight.services.run_failures`),
the flaky card (:mod:`~qasight.services.flaky_service`) and the cycle page's
"Got slower" list (:mod:`~qasight.services.test_history`), so the dashboard
never says something those pages don't.
"""

from __future__ import annotations

from dataclasses import dataclass

from qasight.history.read_model import latest_run_per_cycle, list_cycle_runs
from qasight.history.views import CompletedRunView
from qasight.services import flaky_service, test_history
from qasight.services.run_failures import load_run_failures

# Cycles looked at, most recently run first; older ones aren't news.
MAX_CYCLES = 10
# Tests listed per kind (newly flaky, got slower) across all cycles.
MAX_TESTS = 5


@dataclass(frozen=True)
class FailingCycle:
    cycle: str
    run_id: str
    created_at: float
    branch: str | None
    failed_tests: int
    new_failures: int
    # This run and the ones before it that also failed, back to the last green one.
    failing_runs: int


@dataclass(frozen=True)
class AttentionTest:
    cycle: str
    key: str
    name: str
    # Got slower: recent median minus the earlier one. Newly flaky: None.
    slowdown_ms: int | None = None
    slowdown_pct: float | None = None


@dataclass(frozen=True)
class Attention:
    cycles: int
    passing_cycles: int
    failing: tuple[FailingCycle, ...]
    newly_flaky: tuple[AttentionTest, ...]
    slower: tuple[AttentionTest, ...]


def _failed(run: CompletedRunView) -> bool:
    return run.returncode != 0 or run.status == "failed"


def _failing_streak(run: CompletedRunView, history: list[CompletedRunView]) -> int:
    """Failed runs of the cycle in a row, ending at ``run``, on its branch when it has one."""
    streak = 0
    for earlier in history:
        if run.branch and earlier.branch not in (run.branch, None):
            continue
        if not _failed(earlier):
            break
        streak += 1
    return streak


def needs_attention(*, max_cycles: int = MAX_CYCLES) -> Attention:
    """Red cycles first (most new failures first), then newly flaky and slower tests."""
    latest = list(latest_run_per_cycle().values())[: max(1, max_cycles)]
    failing: list[FailingCycle] = []
    flaky: list[AttentionTest] = []
    slower: list[AttentionTest] = []
    for run in latest:
        cycle = run.cycle or ""
        if _failed(run):
            failures = load_run_failures(run.run_id)
            history = [run, *list_cycle_runs(cycle, before_run_id=run.run_id)]
            failing.append(
                FailingCycle(
                    cycle=cycle,
                    run_id=run.run_id,
                    created_at=run.created_at,
                    branch=run.branch,
                    failed_tests=len(failures.tests) if failures else (run.failed or 0),
                    new_failures=failures.new_failures if failures else 0,
                    failing_runs=max(1, _failing_streak(run, history)),
                )
            )
        flaky += [
            AttentionTest(cycle=cycle, key=t.key, name=t.name)
            for t in flaky_service.newly_flaky_tests(cycle=cycle, run_id=run.run_id)
        ]
        slower += [
            AttentionTest(
                cycle=cycle,
                key=t.key,
                name=t.name,
                slowdown_ms=t.slowdown_ms,
                slowdown_pct=t.slowdown_pct,
            )
            for t in test_history.list_cycle_tests(cycle).tests
            if t.slowdown_ms
        ]
    failing.sort(key=lambda c: (-c.new_failures, -c.created_at))
    slower.sort(key=lambda t: -(t.slowdown_ms or 0))
    return Attention(
        cycles=len(latest),
        passing_cycles=len(latest) - len(failing),
        failing=tuple(failing),
        newly_flaky=tuple(flaky[:MAX_TESTS]),
        slower=tuple(slower[:MAX_TESTS]),
    )
