"""The manager report: how testing went over a period, for people who don't read every run.

One payload feeds every format: the UI's ``/report`` page (printed to PDF by the
browser), ``testo export`` (PDF, PNG, HTML, XLSX, PowerPoint, CSV, JSON) and the
API's downloads. It reuses what the weekly digest and the health score already
compute, so the numbers match ``testo notify --digest`` and the ``/health`` page:

* headline numbers for the period against the one before (pass rate, green
  builds, runs, line coverage) and the project health score;
* a day-by-day trend of runs, green runs and pass rate;
* one row per cycle;
* what needs attention (still failing, turned flaky, got slower) and good news
  (fixed), plus the health score's own findings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from testo_core.history.read_model import list_recent_runs
from testo_core.services import run_digest
from testo_core.services.project_health import grade_for, project_health

PERIOD_DAYS: dict[str, int] = {"day": 1, "week": 7, "month": 30, "quarter": 90}
PERIOD_LABELS: dict[str, str] = {
    "day": "Daily",
    "week": "Weekly",
    "month": "Monthly",
    "quarter": "Quarterly",
}
MAX_ITEMS = 10
_SCAN_LIMIT = 5000


@dataclass(frozen=True)
class TrendPoint:
    day: str  # YYYY-MM-DD (UTC)
    runs: int
    green: int
    pass_rate: float | None


@dataclass(frozen=True)
class CycleRow:
    cycle: str
    runs: int
    green: int
    pass_rate: float | None
    prev_pass_rate: float | None
    failing: int
    newly_flaky: int
    fixed: int
    slower: int
    health_score: int | None


@dataclass(frozen=True)
class NamedTest:
    cycle: str
    name: str
    detail: str = ""


@dataclass(frozen=True)
class Finding:
    cycle: str
    title: str
    detail: str


@dataclass(frozen=True)
class Kpis:
    health_score: int | None
    health_grade: str | None
    pass_rate: float | None
    prev_pass_rate: float | None
    runs: int
    prev_runs: int
    green: int
    prev_green: int
    coverage_pct: float | None
    prev_coverage_pct: float | None
    cycles: int
    failing_tests: int
    newly_flaky: int
    fixed: int


@dataclass(frozen=True)
class ManagerReport:
    title: str
    period: str
    days: int
    start: float
    end: float
    generated_at: float
    scope: list[str]
    summary: list[str]
    kpis: Kpis
    trend: list[TrendPoint]
    cycles: list[CycleRow]
    still_failing: list[NamedTest] = field(default_factory=list)
    newly_flaky: list[NamedTest] = field(default_factory=list)
    fixed: list[NamedTest] = field(default_factory=list)
    slower: list[NamedTest] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def _combined_rate(rows: list[run_digest.CycleDigest], *, prev: bool) -> float | None:
    """Pass rate over every cycle, weighted by runs (each cycle's rate is per test)."""
    pairs: list[tuple[float, int]] = []
    for d in rows:
        rate = d.prev_pass_rate if prev else d.pass_rate
        runs = d.prev_runs if prev else d.runs
        if rate is not None and runs:
            pairs.append((rate, runs))
    total = sum(n for _, n in pairs)
    return round(sum(rate * n for rate, n in pairs) / total, 1) if total else None


def _coverage(rows: list[run_digest.CycleDigest], *, prev: bool) -> float | None:
    values = [d.prev_coverage_pct if prev else d.coverage_pct for d in rows]
    known = [v for v in values if v is not None]
    return round(sum(known) / len(known), 1) if known else None


def _trend(start: float, end: float, days: int, wanted: set[str]) -> list[TrendPoint]:
    first = datetime.fromtimestamp(start, tz=UTC).date()
    buckets: dict[str, list[Any]] = {
        (first + timedelta(days=i + 1)).isoformat(): [] for i in range(days)
    }
    for run in list_recent_runs(limit=_SCAN_LIMIT):  # newest first
        if run.created_at < start:
            break
        if run.created_at > end or not run.cycle or (wanted and run.cycle not in wanted):
            continue
        day = datetime.fromtimestamp(run.created_at, tz=UTC).date().isoformat()
        buckets.setdefault(day, []).append(run)
    points = []
    for day in sorted(buckets):
        runs = buckets[day]
        passed = sum(r.passed or 0 for r in runs)
        bad = sum((r.failed or 0) + (r.broken or 0) for r in runs)
        points.append(
            TrendPoint(
                day=day,
                runs=len(runs),
                green=sum(1 for r in runs if r.returncode == 0),
                pass_rate=_pct(passed, passed + bad),
            )
        )
    return points[-days:]


def _direction(now: float | None, before: float | None, what: str) -> str | None:
    if now is None:
        return None
    if before is None:
        return f"{what} was {now:.1f}%."
    delta = now - before
    if abs(delta) < 0.05:
        return f"{what} held at {now:.1f}%."
    verb = "rose" if delta > 0 else "fell"
    return f"{what} {verb} from {before:.1f}% to {now:.1f}%."


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _summary(k: Kpis, period: str) -> list[str]:
    if not k.runs:
        return [f"No runs were recorded this {period}."]
    lines = [
        f"{_plural(k.runs, 'run')} across {_plural(k.cycles, 'cycle')}; "
        f"{k.green} green ({_pct(k.green, k.runs) or 0:.0f}%)."
    ]
    for line in (
        _direction(k.pass_rate, k.prev_pass_rate, "The test pass rate"),
        _direction(k.coverage_pct, k.prev_coverage_pct, "Line coverage"),
    ):
        if line:
            lines.append(line)
    changes = []
    if k.failing_tests:
        changes.append(f"{_plural(k.failing_tests, 'test')} still failing")
    if k.newly_flaky:
        changes.append(f"{_plural(k.newly_flaky, 'test')} turned flaky")
    if k.fixed:
        changes.append(f"{_plural(k.fixed, 'test')} fixed")
    if changes:
        lines.append(", ".join(changes).capitalize() + ".")
    else:
        lines.append("No failing or newly flaky tests.")
    if k.health_score is not None:
        lines.append(f"Test health score: {k.health_score}/100 (grade {k.health_grade}).")
    return lines


def period_title(period: str) -> str:
    return f"{PERIOD_LABELS.get(period, 'Test')} test report"


def build_report(
    *,
    period: str = "week",
    cycles: list[str] | None = None,
    now: float | None = None,
    title: str | None = None,
) -> ManagerReport:
    """The report for the last ``period`` (day, week, month or quarter) of ``cycles`` (all when empty)."""
    if period not in PERIOD_DAYS:
        raise ValueError(f"period must be one of {', '.join(PERIOD_DAYS)}")
    days = PERIOD_DAYS[period]
    wanted = [c for c in dict.fromkeys(cycles or ()) if c]
    digest = run_digest.build_digest(period="week", cycles=wanted or None, now=now, days=days)
    rows = list(digest.cycles)

    health = project_health(cycle=wanted[0] if len(wanted) == 1 else None)
    health_by_cycle = {c.cycle: c.score for c in health.by_cycle}
    if len(wanted) > 1:
        scores = [health_by_cycle[c] for c in wanted if health_by_cycle.get(c) is not None]
        known = [s for s in scores if s is not None]
        score: int | None = round(sum(known) / len(known)) if known else None
    else:
        score = health.score

    kpis = Kpis(
        health_score=score,
        health_grade=grade_for(score),
        pass_rate=_combined_rate(rows, prev=False),
        prev_pass_rate=_combined_rate(rows, prev=True),
        runs=sum(d.runs for d in rows),
        prev_runs=sum(d.prev_runs for d in rows),
        green=sum(d.green for d in rows),
        prev_green=sum(d.prev_green for d in rows),
        coverage_pct=_coverage(rows, prev=False),
        prev_coverage_pct=_coverage(rows, prev=True),
        cycles=len(rows),
        failing_tests=sum(len(d.still_failing) for d in rows),
        newly_flaky=sum(len(d.newly_flaky) for d in rows),
        fixed=sum(len(d.fixed) for d in rows),
    )
    in_scope = set(wanted)
    findings = [
        Finding(cycle=f.cycle, title=f.title, detail=f.detail)
        for f in health.findings
        if not in_scope or f.cycle in in_scope
    ][:MAX_ITEMS]
    return ManagerReport(
        title=title or period_title(period),
        period=period,
        days=days,
        start=digest.start,
        end=digest.end,
        generated_at=digest.end if now is not None else datetime.now(tz=UTC).timestamp(),
        scope=wanted,
        summary=_summary(kpis, period),
        kpis=kpis,
        trend=_trend(digest.start, digest.end, days, in_scope),
        cycles=[
            CycleRow(
                cycle=d.cycle,
                runs=d.runs,
                green=d.green,
                pass_rate=d.pass_rate,
                prev_pass_rate=d.prev_pass_rate,
                failing=len(d.still_failing),
                newly_flaky=len(d.newly_flaky),
                fixed=len(d.fixed),
                slower=len(d.slower),
                health_score=health_by_cycle.get(d.cycle),
            )
            for d in rows
        ],
        still_failing=[NamedTest(d.cycle, n) for d in rows for n in d.still_failing][:MAX_ITEMS],
        newly_flaky=[NamedTest(d.cycle, n) for d in rows for n in d.newly_flaky][:MAX_ITEMS],
        fixed=[NamedTest(d.cycle, n) for d in rows for n in d.fixed][:MAX_ITEMS],
        slower=[
            NamedTest(
                d.cycle,
                s.name,
                f"{s.before_ms / 1000:.2f}s → {s.after_ms / 1000:.2f}s",
            )
            for d in rows
            for s in d.slower
        ][:MAX_ITEMS],
        findings=findings,
    )
