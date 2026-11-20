"""What's new in one run: the answer ``qasight run`` and ``qasight ingest`` end with.

QASight's promise is "what's new, what's flaky and what got slower". ``qasight
run`` used to end with counts and "see it with ``qasight ui``", so a person or a
coding agent had to run ``summary`` and ``flaky`` to get that answer. This
builds it for the run just recorded, from the same services as the run page
(:mod:`~qasight.services.run_failures`), the flaky list
(:mod:`~qasight.services.flaky_service`) and the cycle page's "Got slower" list
(:mod:`~qasight.services.test_history`), so the terminal never says something
the UI doesn't.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from qasight.history.read_model import get_run, list_cycle_runs
from qasight.history.views import CompletedRunView

# Tests listed per kind; the rest are counted.
MAX_LISTED = 5
_FAILED = frozenset({"failed", "broken"})


@dataclass(frozen=True)
class NewsFailure:
    name: str
    full_name: str
    message: str
    # Failed runs in a row, this one included.
    failing_runs: int
    flaky: bool


@dataclass(frozen=True)
class NewsSlower:
    name: str
    median_ms: int | None
    last_ms: int | None
    slowdown_ms: int
    slowdown_pct: float | None


@dataclass(frozen=True)
class RunNews:
    run_id: str
    cycle: str
    outcome: str  # "passed" or "failed"
    total: int
    passed: int
    failed: int
    skipped: int
    # The run it was compared with (a PR's target branch, else the previous run).
    baseline_run_id: str | None
    new_failures: tuple[NewsFailure, ...] = ()
    known_failures: tuple[NewsFailure, ...] = ()
    fixed: tuple[str, ...] = ()
    newly_flaky: tuple[str, ...] = ()
    slower: tuple[NewsSlower, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _failed(run: CompletedRunView) -> bool:
    return run.returncode != 0 or (run.failed or 0) + (run.broken or 0) > 0


def _previous(run: CompletedRunView) -> CompletedRunView | None:
    if not run.cycle:
        return None
    for older in list_cycle_runs(run.cycle, before_run_id=run.run_id):
        if not run.branch or older.branch in (run.branch, None):
            return older
    return None


def run_news(run_id: str) -> RunNews | None:
    """What's new in *run_id*; ``None`` when the run isn't in the history."""
    from qasight.history.cases import cases_for_run
    from qasight.services import flaky_service, test_history
    from qasight.services.run_failures import load_run_failures

    run = get_run(run_id=run_id)
    if run is None:
        return None
    cycle = run.cycle or ""
    failures = load_run_failures(run.run_id)
    tests = failures.tests if failures else ()

    def as_news(t: Any) -> NewsFailure:
        first = (t.message or "").strip().splitlines()
        return NewsFailure(
            name=t.name,
            full_name=t.full_name,
            message=first[0][:200] if first else "",
            failing_runs=t.failing_runs,
            flaky=t.flaky,
        )

    new = [as_news(t) for t in tests if t.new]
    known = [as_news(t) for t in tests if not t.new]

    baseline_id = failures.baseline_run_id if failures else None
    baseline = get_run(run_id=baseline_id) if baseline_id else _previous(run)
    fixed: list[str] = []
    if baseline is not None:
        now = cases_for_run(run)
        before = cases_for_run(baseline)
        fixed = sorted(
            str(case.get("name") or key)
            for key, case in before.items()
            if case.get("status") in _FAILED and (now.get(key) or {}).get("status") == "passed"
        )

    flaky: list[str] = []
    slower: list[NewsSlower] = []
    if cycle:
        try:
            flaky = [
                t.name for t in flaky_service.newly_flaky_tests(cycle=cycle, run_id=run.run_id)
            ]
        except Exception:  # noqa: BLE001 — never fail a run over its summary
            flaky = []
        try:
            here = set(cases_for_run(run))
            slower = [
                NewsSlower(
                    name=t.name,
                    median_ms=t.median_duration_ms,
                    last_ms=t.last_duration_ms,
                    slowdown_ms=int(t.slowdown_ms or 0),
                    slowdown_pct=t.slowdown_pct,
                )
                for t in test_history.list_cycle_tests(cycle).tests
                if t.slowdown_ms and (not here or t.key in here)
            ]
            slower.sort(key=lambda s: -s.slowdown_ms)
        except Exception:  # noqa: BLE001
            slower = []

    return RunNews(
        run_id=run.run_id,
        cycle=cycle,
        outcome="failed" if _failed(run) else "passed",
        total=int(run.total_tests or 0),
        passed=int(run.passed or 0),
        failed=int((run.failed or 0) + (run.broken or 0)),
        skipped=int(run.skipped or 0),
        baseline_run_id=baseline.run_id if baseline is not None else None,
        new_failures=tuple(new[:MAX_LISTED]),
        known_failures=tuple(known[:MAX_LISTED]),
        fixed=tuple(fixed[:MAX_LISTED]),
        newly_flaky=tuple(flaky[:MAX_LISTED]),
        slower=tuple(slower[:MAX_LISTED]),
        counts={
            "new_failures": len(new),
            "known_failures": len(known),
            "fixed": len(fixed),
            "newly_flaky": len(flaky),
            "slower": len(slower),
        },
    )


def _ms(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{value / 1000:.1f}s" if value >= 1000 else f"{value} ms"


def render_lines(news: RunNews) -> list[str]:
    """The plain-text block printed after a run (no colour codes; callers add style)."""
    head = f"{news.passed} passed, {news.failed} failed"
    if news.skipped:
        head += f", {news.skipped} skipped"
    head += f"  ·  cycle {news.cycle}"
    head += "  ·  vs previous run" if news.baseline_run_id else "  ·  first run of this cycle"
    lines = [head]

    def more(kind: str, shown: int) -> None:
        extra = news.counts.get(kind, 0) - shown
        if extra > 0:
            lines.append(f"  … and {extra} more")

    for t in news.new_failures:
        why = f": {t.message}" if t.message else ""
        lines.append(f"  New failure   {t.name}{why}")
    more("new_failures", len(news.new_failures))
    for t in news.known_failures:
        lines.append(f"  Still failing {t.name} ({t.failing_runs} runs in a row)")
    more("known_failures", len(news.known_failures))
    for name in news.newly_flaky:
        lines.append(f"  Newly flaky   {name}")
    more("newly_flaky", len(news.newly_flaky))
    for s in news.slower:
        lines.append(f"  Slower        {s.name} {_ms(s.median_ms)} -> {_ms(s.last_ms)}")
    more("slower", len(news.slower))
    for name in news.fixed:
        lines.append(f"  Fixed         {name}")
    more("fixed", len(news.fixed))
    if len(lines) == 1 and news.baseline_run_id:
        lines.append("  Nothing new since the previous run.")
    lines.append("Details: qasight summary · qasight flaky · qasight ui")
    return lines


def case_changes(baseline: CompletedRunView, current: CompletedRunView) -> list[dict[str, Any]]:
    """Per-test changes between two runs from the run history alone.

    For runs whose report copy holds no per-test files (``run -- <command>``,
    ``--no-report-db``), so ``summary`` still names what broke and what was fixed.
    """
    from qasight.history.cases import cases_for_run

    before = cases_for_run(baseline)
    now = cases_for_run(current)
    out: list[dict[str, Any]] = []
    for key, case in now.items():
        old = before.get(key)
        status = str(case.get("status") or "")
        if old is None:
            kind = "added"
        else:
            was = str(old.get("status") or "")
            if was == status:
                continue
            if status in _FAILED and was not in _FAILED:
                kind = "regression"
            elif was in _FAILED and status == "passed":
                kind = "fix"
            else:
                kind = "status_change"
        old_ms = (old or {}).get("duration_ms")
        new_ms = case.get("duration_ms")
        out.append(
            {
                "name": str(case.get("name") or key),
                "group": str(case.get("group") or ""),
                "kind": kind,
                "baseline_status": (old or {}).get("status"),
                "current_status": status,
                "duration_delta_ms": (
                    int(new_ms) - int(old_ms) if new_ms is not None and old_ms is not None else None
                ),
            }
        )
    for key, case in before.items():
        if key not in now:
            out.append(
                {
                    "name": str(case.get("name") or key),
                    "group": str(case.get("group") or ""),
                    "kind": "removed",
                    "baseline_status": case.get("status"),
                    "current_status": None,
                    "duration_delta_ms": None,
                }
            )
    order = {"regression": 0, "fix": 1, "status_change": 2, "added": 3, "removed": 4}
    out.sort(key=lambda c: (order.get(c["kind"], 9), c["name"]))
    return out
