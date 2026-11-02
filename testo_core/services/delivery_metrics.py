"""Delivery metrics: how long tests stay red, what the suite costs, and how often main is red.

Three numbers managers ask for that a single run can't answer, all computed from
history that is already stored (the run's time, exit code, branch and per-test
results):

* **Time to fix.** A test that starts failing opens a *red spell*; the first run
  where it passes again closes it. The median spell closed in the period is the
  time to fix (the test-side "time to restore"). Spells still open at the end are
  *open failures*, counted by age. Flaky tests are left out (they are on the flaky
  list), and so is a pass on the same commit that failed, which is a retry, not a fix.
* **Suite time.** Wall time of each run (median and slowest tenth), the CI time
  every run took together, and the stage that grew most.
* **Main branch health.** Share of the period the default branch was red (from a
  red run until the next green one), its longest red spell, and how many pull
  request commits passed on their first run.

The default branch is the most common pull request target (``ci_base_ref``), else
``main``, ``master`` or ``trunk`` if runs were recorded on one. When no run has a
branch at all, every run counts as the default branch, so a project that never
sends branch data still gets numbers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any

from testo_core.history.views import CompletedRunView
from testo_core.services import flaky_service
from testo_core.services.delta_service import within_noise

_PASS = "passed"
_FAIL = frozenset({"failed", "broken"})
_WELL_KNOWN = ("main", "master", "trunk")
DAY = 86400.0
# How far back red spells are traced, so a failure that began before the period still counts.
LOOKBACK_DAYS = 90
MAX_CASE_RUNS = 400  # per cycle: older runs only matter for spells this old
MAX_OPEN = 10
AGE_BUCKETS: tuple[tuple[str, float], ...] = (
    ("Under a day", DAY),
    ("1–7 days", 7 * DAY),
    ("1–4 weeks", 30 * DAY),
    ("Over a month", float("inf")),
)


@dataclass(frozen=True)
class AgeBucket:
    label: str
    count: int


@dataclass(frozen=True)
class OpenFailure:
    cycle: str
    name: str
    since: float  # when the red spell began (epoch seconds)
    age_s: float
    runs: int  # runs on the default branch it failed in, in a row
    key: str = ""


@dataclass(frozen=True)
class CycleDelivery:
    cycle: str
    fixes: int = 0
    median_fix_s: float | None = None
    prev_median_fix_s: float | None = None
    open_failures: int = 0
    suite_median_ms: int | None = None
    suite_p90_ms: int | None = None
    prev_suite_median_ms: int | None = None
    ci_minutes: float = 0.0
    prev_ci_minutes: float = 0.0
    # The stage whose median time grew most against the period before, when it grew.
    grown_stage: str | None = None
    grown_stage_before_ms: int | None = None
    grown_stage_after_ms: int | None = None
    red_pct: float | None = None
    prev_red_pct: float | None = None
    longest_red_s: float | None = None
    first_try_green_pct: float | None = None
    first_try_commits: int = 0


@dataclass(frozen=True)
class Delivery:
    """Every cycle in scope together, plus one row per cycle."""

    default_branch: str | None = None  # None: no run recorded a branch, so every run counts
    fixes: int = 0
    median_fix_s: float | None = None
    prev_median_fix_s: float | None = None
    open_failures: int = 0
    open_by_age: list[AgeBucket] = field(default_factory=list)
    oldest_open: list[OpenFailure] = field(default_factory=list)
    ci_minutes: float = 0.0
    prev_ci_minutes: float = 0.0
    red_pct: float | None = None
    prev_red_pct: float | None = None
    longest_red_s: float | None = None
    first_try_green_pct: float | None = None
    prev_first_try_green_pct: float | None = None
    first_try_commits: int = 0
    cycles: list[CycleDelivery] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -- helpers -----------------------------------------------------------------------


def default_branch(runs: list[CompletedRunView]) -> str | None:
    """The branch pull requests target most, else a well-known one with runs; None if no branches."""
    targets = Counter(r.base_branch for r in runs if r.base_branch)
    if targets:
        return targets.most_common(1)[0][0]
    branches = {r.branch for r in runs if r.branch}
    return next((b for b in _WELL_KNOWN if b in branches), None)


def on_default_branch(run: CompletedRunView, branch: str | None, any_branch: bool) -> bool:
    if not any_branch:
        return True  # no run has a branch: they all count
    return branch is not None and run.branch == branch


def _p90(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, -(-len(ordered) * 9 // 10) - 1)]


def _red(run: CompletedRunView) -> bool:
    return run.returncode != 0


# -- time to fix -------------------------------------------------------------------


@dataclass
class _Spell:
    since: float
    commit: str | None
    runs: int = 1


def red_spells(
    runs: list[CompletedRunView], cycle: str
) -> tuple[list[tuple[str, float, float]], list[OpenFailure]]:
    """(closed spells as (test key, fixed_at, seconds red), open failures) for ``runs`` (oldest first)."""
    cases = [(run, flaky_service._cases_for_run(run)) for run in runs]
    cases = [(run, c) for run, c in cases if c]
    observations: dict[str, list[dict[str, Any]]] = {}
    for run, run_cases in cases:
        for key, case in run_cases.items():
            observations.setdefault(key, []).append(flaky_service.observation(run, case))
    flaky = {key for key, obs in observations.items() if flaky_service.classify(key, obs)}

    open_: dict[str, _Spell] = {}
    names: dict[str, str] = {}
    closed: list[tuple[str, float, float]] = []
    for run, run_cases in cases:
        for key, case in run_cases.items():
            if key in flaky:
                continue
            status = str(case.get("status"))
            names[key] = str(case.get("name") or key)
            spell = open_.get(key)
            if status in _FAIL:
                if spell is None:
                    open_[key] = _Spell(since=run.created_at, commit=run.commit)
                else:
                    spell.runs += 1
            elif status == _PASS and spell is not None:
                del open_[key]
                if spell.commit and spell.commit == run.commit:
                    continue  # passed again on the commit that failed: a retry, not a fix
                closed.append((key, run.created_at, max(0.0, run.created_at - spell.since)))
    latest = cases[-1][1] if cases else {}
    end = cases[-1][0].created_at if cases else 0.0
    still = [
        OpenFailure(cycle, names[key], s.since, max(0.0, end - s.since), s.runs, key)
        for key, s in open_.items()
        if str(latest.get(key, {}).get("status")) in _FAIL  # gone or skipped since: not open
    ]
    return closed, still


# -- main branch red time ----------------------------------------------------------


def _red_intervals(runs: list[CompletedRunView], end: float) -> list[tuple[float, float]]:
    """(from, to) spells the branch was red: a red run until the next green one (oldest first)."""
    spells: list[tuple[float, float]] = []
    began: float | None = None
    for run in runs:
        if _red(run):
            if began is None:
                began = run.created_at
        elif began is not None:
            spells.append((began, run.created_at))
            began = None
    if began is not None:
        spells.append((began, end))
    return spells


def _clip(spells: list[tuple[float, float]], lo: float, hi: float) -> list[tuple[float, float]]:
    return [(max(a, lo), min(b, hi)) for a, b in spells if b > lo and a < hi]


def _union_seconds(spells: list[tuple[float, float]]) -> float:
    total, cur_a, cur_b = 0.0, None, None
    for a, b in sorted(spells):
        if cur_b is None or a > cur_b:
            if cur_b is not None and cur_a is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_a is not None and cur_b is not None:
        total += cur_b - cur_a
    return total


def _observed_from(runs: list[CompletedRunView], lo: float) -> float | None:
    """Where the measured span starts: ``lo`` if a run came before it, else the first run in it."""
    if any(r.created_at < lo for r in runs):
        return lo
    inside = [r.created_at for r in runs if r.created_at >= lo]
    return min(inside) if inside else None


def _red_pct(spells: list[tuple[float, float]], lo: float | None, hi: float) -> float | None:
    if lo is None or hi <= lo:
        return None
    return round(100.0 * _union_seconds(_clip(spells, lo, hi)) / (hi - lo), 1)


def _first_try(
    runs: list[CompletedRunView], branch: str | None, lo: float, hi: float
) -> tuple[int, int]:
    """(commits green on their first run, commits) for pull request runs that started in [lo, hi)."""
    first: dict[tuple[str | None, str], CompletedRunView] = {}
    for run in runs:  # oldest first
        if not run.commit or not run.base_branch or run.branch == branch:
            continue
        first.setdefault((run.branch, run.commit), run)
    picked = [r for r in first.values() if lo <= r.created_at < hi]
    return sum(1 for r in picked if not _red(r)), len(picked)


# -- suite time --------------------------------------------------------------------


def _stage_medians(runs: list[CompletedRunView]) -> dict[str, int]:
    times: dict[str, list[float]] = {}
    for run in runs:
        for stage in run.stage_health:
            secs = stage.get("duration_s")
            if stage.get("name") and isinstance(secs, int | float):
                times.setdefault(str(stage["name"]), []).append(float(secs) * 1000)
    return {name: int(median(v)) for name, v in times.items() if v}


def _grown_stage(
    now: list[CompletedRunView], prev: list[CompletedRunView]
) -> tuple[str, int, int] | None:
    after, before = _stage_medians(now), _stage_medians(prev)
    best: tuple[str, int, int] | None = None
    for name, a in after.items():
        b = before.get(name)
        if b is None or a <= b or within_noise(absolute_delta=a - b, baseline=b, floor_ms=1000):
            continue
        if best is None or a - b > best[2] - best[1]:
            best = (name, b, a)
    return best


def _durations(runs: list[CompletedRunView]) -> list[float]:
    return [r.wall_duration_ms for r in runs if r.wall_duration_ms > 0]


# -- building ----------------------------------------------------------------------


def _cycle(
    cycle: str,
    runs: list[CompletedRunView],
    *,
    branch: str | None,
    any_branch: bool,
    start: float,
    end: float,
    prev_start: float,
) -> tuple[CycleDelivery, list[float], list[float], list[OpenFailure], list[tuple[float, float]]]:
    """One cycle's row, plus the raw pieces the totals are built from."""
    on_default = [r for r in runs if on_default_branch(r, branch, any_branch)]
    closed, still = red_spells(on_default[-MAX_CASE_RUNS:], cycle)
    fixes_now = [secs for _, at, secs in closed if start <= at <= end]
    fixes_prev = [secs for _, at, secs in closed if prev_start <= at < start]

    now_runs = [r for r in runs if start <= r.created_at <= end]
    prev_runs = [r for r in runs if prev_start <= r.created_at < start]
    now_ms, prev_ms = _durations(now_runs), _durations(prev_runs)
    grown = _grown_stage(now_runs, prev_runs)

    spells = _red_intervals(on_default, end)
    ok, tried = _first_try(runs, branch, start, end) if any_branch else (0, 0)
    longest = [b - a for a, b in spells if b > start]
    row = CycleDelivery(
        cycle=cycle,
        fixes=len(fixes_now),
        median_fix_s=median(fixes_now) if fixes_now else None,
        prev_median_fix_s=median(fixes_prev) if fixes_prev else None,
        open_failures=len(still),
        suite_median_ms=int(median(now_ms)) if now_ms else None,
        suite_p90_ms=int(_p90(now_ms)) if now_ms else None,
        prev_suite_median_ms=int(median(prev_ms)) if prev_ms else None,
        ci_minutes=round(sum(now_ms) / 60000, 1),
        prev_ci_minutes=round(sum(prev_ms) / 60000, 1),
        grown_stage=grown[0] if grown else None,
        grown_stage_before_ms=grown[1] if grown else None,
        grown_stage_after_ms=grown[2] if grown else None,
        red_pct=_red_pct(spells, _observed_from(on_default, start), end),
        prev_red_pct=_red_pct(spells, _observed_from(on_default, prev_start), start),
        longest_red_s=max(longest) if longest else None,
        first_try_green_pct=round(100.0 * ok / tried, 1) if tried else None,
        first_try_commits=tried,
    )
    return row, fixes_now, fixes_prev, still, spells


def build_delivery(
    runs: list[CompletedRunView],
    *,
    start: float,
    end: float,
    cycles: set[str] | None = None,
) -> Delivery:
    """Delivery metrics for [start, end] against the period of the same length before it.

    ``runs`` may be in any order and cover any span; runs after ``end`` and older than
    :data:`LOOKBACK_DAYS` before the period are ignored.
    """
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

    rows: list[CycleDelivery] = []
    fixes_now: list[float] = []
    fixes_prev: list[float] = []
    still: list[OpenFailure] = []
    spells: list[tuple[float, float]] = []
    default_runs: list[CompletedRunView] = []
    for cycle, cycle_runs in by_cycle.items():
        if not any(prev_start <= r.created_at <= end for r in cycle_runs):
            continue
        row, now_f, prev_f, open_f, red = _cycle(
            cycle,
            cycle_runs,
            branch=branch,
            any_branch=any_branch,
            start=start,
            end=end,
            prev_start=prev_start,
        )
        rows.append(row)
        fixes_now += now_f
        fixes_prev += prev_f
        still += open_f
        spells += red
        default_runs += [r for r in cycle_runs if on_default_branch(r, branch, any_branch)]
    rows.sort(key=lambda r: (-r.ci_minutes, r.cycle))

    ok, tried = _first_try(kept, branch, start, end) if any_branch else (0, 0)
    ok_prev, tried_prev = _first_try(kept, branch, prev_start, start) if any_branch else (0, 0)
    still.sort(key=lambda f: (-f.age_s, f.cycle, f.name))
    longest = [b - a for a, b in spells if b > start]
    return Delivery(
        default_branch=branch,
        fixes=len(fixes_now),
        median_fix_s=median(fixes_now) if fixes_now else None,
        prev_median_fix_s=median(fixes_prev) if fixes_prev else None,
        open_failures=len(still),
        open_by_age=_age_buckets(still),
        oldest_open=still[:MAX_OPEN],
        ci_minutes=round(sum(r.ci_minutes for r in rows), 1),
        prev_ci_minutes=round(sum(r.prev_ci_minutes for r in rows), 1),
        red_pct=_red_pct(spells, _observed_from(default_runs, start), end),
        prev_red_pct=_red_pct(spells, _observed_from(default_runs, prev_start), start),
        longest_red_s=max(longest) if longest else None,
        first_try_green_pct=round(100.0 * ok / tried, 1) if tried else None,
        prev_first_try_green_pct=round(100.0 * ok_prev / tried_prev, 1) if tried_prev else None,
        first_try_commits=tried,
        cycles=rows,
    )


def _age_buckets(still: list[OpenFailure]) -> list[AgeBucket]:
    counts = dict.fromkeys((label for label, _ in AGE_BUCKETS), 0)
    for f in still:
        label = next(label for label, upto in AGE_BUCKETS if f.age_s < upto)
        counts[label] += 1
    return [AgeBucket(label, n) for label, n in counts.items()]


def span(seconds: float | None) -> str:
    """``45m``, ``5h``, ``2.5d``: a short duration for headlines."""
    if seconds is None:
        return "–"
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))}m"
    if seconds < DAY:
        return f"{seconds / 3600:.0f}h"
    days = seconds / DAY
    return f"{days:.0f}d" if days >= 10 else f"{days:.1f}d"
