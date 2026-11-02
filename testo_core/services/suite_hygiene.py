"""Suite growth and hygiene: is testing keeping up with the code, and what is dead weight?

* **Growth.** Tests in each cycle's latest run at the end of the period against the
  end of the period before, and which tests were added or removed between those
  two runs (by test key, so a renamed test shows as one removed and one added).
* **Always skipped.** Tests skipped in every run of the period (at least
  :data:`MIN_RUNS` runs): they cost nothing but also check nothing.
* **Slow and never failed.** Tests in the slowest tenth of the cycle (by median
  time) that passed every run over both periods (at least :data:`MIN_RUNS`). Not
  wrong in itself, but the first place to look when the suite is too slow: maybe
  they belong in a nightly cycle, or cover what a faster test already does.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any

from testo_core.history.views import CompletedRunView
from testo_core.services import flaky_service

MIN_RUNS = 3
MAX_NAMES = 10
_PASS = "passed"
_SKIP = frozenset({"skipped"})


@dataclass(frozen=True)
class NamedCycleTest:
    cycle: str
    name: str
    detail: str = ""


@dataclass(frozen=True)
class CycleSize:
    cycle: str
    tests: int
    prev_tests: int | None
    added: int
    removed: int


@dataclass(frozen=True)
class Hygiene:
    tests: int = 0
    prev_tests: int | None = None
    added: int = 0
    removed: int = 0
    cycles: list[CycleSize] = field(default_factory=list)
    added_tests: list[NamedCycleTest] = field(default_factory=list)
    removed_tests: list[NamedCycleTest] = field(default_factory=list)
    always_skipped: list[NamedCycleTest] = field(default_factory=list)
    always_skipped_count: int = 0
    slow_never_failed: list[NamedCycleTest] = field(default_factory=list)
    slow_never_failed_ms: int = 0  # their median times added up: one run's worth

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _seconds(ms: float) -> str:
    return f"{ms / 1000:.1f}s" if ms < 60_000 else f"{ms / 60000:.1f} min"


def build_hygiene(
    runs: list[CompletedRunView],
    *,
    start: float,
    end: float,
    cycles: set[str] | None = None,
) -> Hygiene:
    """Suite size, added and removed tests, always-skipped and slow never-failing tests."""
    prev_start = start - (end - start)
    by_cycle: dict[str, list[CompletedRunView]] = {}
    for run in sorted(runs, key=lambda r: r.created_at):
        if (
            run.cycle
            and (not cycles or run.cycle in cycles)
            and prev_start <= run.created_at <= end
        ):
            by_cycle.setdefault(run.cycle, []).append(run)

    sizes: list[CycleSize] = []
    added: list[NamedCycleTest] = []
    removed: list[NamedCycleTest] = []
    skipped: list[NamedCycleTest] = []
    slow: list[tuple[int, NamedCycleTest]] = []
    for cycle, cycle_runs in by_cycle.items():
        with_cases = [(r, c) for r in cycle_runs if (c := flaky_service._cases_for_run(r))]
        now = [(r, c) for r, c in with_cases if r.created_at >= start]
        if not now:
            continue
        before = [(r, c) for r, c in with_cases if r.created_at < start]
        latest = now[-1][1]
        earlier = before[-1][1] if before else None
        names = {k: str(c.get("name") or k) for _, cases in with_cases for k, c in cases.items()}
        new = sorted(set(latest) - set(earlier)) if earlier is not None else []
        gone = sorted(set(earlier) - set(latest)) if earlier is not None else []
        added += [NamedCycleTest(cycle, names[k]) for k in new]
        removed += [NamedCycleTest(cycle, names[k]) for k in gone]
        sizes.append(
            CycleSize(
                cycle=cycle,
                tests=len(latest),
                prev_tests=len(earlier) if earlier is not None else None,
                added=len(new),
                removed=len(gone),
            )
        )

        statuses: dict[str, list[str]] = {}
        durations: dict[str, list[int]] = {}
        for _, cases in with_cases:
            for key, case in cases.items():
                status = str(case.get("status"))
                statuses.setdefault(key, []).append(status)
                ms = case.get("duration_ms")
                if isinstance(ms, int) and status == _PASS:
                    durations.setdefault(key, []).append(ms)
        if len(now) >= MIN_RUNS:
            for key in latest:
                seen = [str(c.get(key, {}).get("status")) for _, c in now if key in c]
                if len(seen) >= MIN_RUNS and all(s in _SKIP for s in seen):
                    skipped.append(
                        NamedCycleTest(cycle, names[key], f"skipped in {len(seen)} runs")
                    )
        medians = {k: int(median(v)) for k, v in durations.items() if v and k in latest}
        if len(medians) >= 10:
            cut = sorted(medians.values())[-max(1, len(medians) // 10)]
            for key, ms in medians.items():
                seen = statuses[key]
                if ms >= cut and len(seen) >= MIN_RUNS and all(s == _PASS for s in seen):
                    slow.append(
                        (
                            ms,
                            NamedCycleTest(
                                cycle,
                                names[key],
                                f"{_seconds(ms)}, passed all {len(seen)} runs",
                            ),
                        )
                    )

    slow.sort(key=lambda p: (-p[0], p[1].cycle, p[1].name))
    known_prev = [s.prev_tests for s in sizes if s.prev_tests is not None]
    sizes.sort(key=lambda s: (-s.tests, s.cycle))
    return Hygiene(
        tests=sum(s.tests for s in sizes),
        # A cycle with no run before the period is new: it had 0 tests then.
        prev_tests=sum(s.prev_tests or 0 for s in sizes) if known_prev else None,
        added=len(added),
        removed=len(removed),
        cycles=sizes,
        added_tests=added[:MAX_NAMES],
        removed_tests=removed[:MAX_NAMES],
        always_skipped=skipped[:MAX_NAMES],
        always_skipped_count=len(skipped),
        slow_never_failed=[t for _, t in slow[:MAX_NAMES]],
        slow_never_failed_ms=sum(ms for ms, _ in slow),
    )
