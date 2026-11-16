"""Numbers for a beta team's case study: ``qasight case-study --cycle shop --since 2026-11-01``.

A design-partner team's story is only as good as its numbers. This reads the run
history of one or more cycles and compares a "before" period with an "after"
period of the same length (by default the days before and after ``--since``, the
day the team switched to QASight):

- runs and test results recorded, and the share of runs that failed;
- median time from a test starting to fail on the default branch to passing again
  (:mod:`qasight.services.delivery_metrics`), and how much of the time it was red;
- failures with a cause marked, how long a cause took to be marked after the
  failure first showed, and how often a suggested cause was right
  (:mod:`qasight.services.similar_causes`).

It prints Markdown ready to paste into the case study
(``docs/Processes & Guides/Beta Case Study Kit.md`` has the interview questions
and the template), or JSON with ``--json``.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class PeriodNumbers:
    start: str
    end: str
    runs: int
    results: int
    failed_runs_pct: float | None
    median_fix_hours: float | None
    red_pct: float | None


@dataclass(frozen=True)
class CaseStudy:
    cycles: list[str]
    since: str
    before: PeriodNumbers
    after: PeriodNumbers
    causes_marked: int
    median_hours_to_cause: float | None
    suggestion_precision: float | None
    suggestion_coverage: float | None
    imported_from_reportportal: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _hours(seconds: float | None) -> float | None:
    return round(seconds / 3600, 1) if seconds is not None else None


def _period(runs: list[Any], cycles: set[str], start: float, end: float) -> PeriodNumbers:
    from qasight.services.delivery_metrics import build_delivery

    inside = [r for r in runs if start <= r.started_at < end]
    failed = sum(1 for r in inside if r.returncode not in (0, None))
    delivery = build_delivery(runs, start=start, end=end, cycles=cycles)
    return PeriodNumbers(
        start=datetime.fromtimestamp(start, tz=UTC).date().isoformat(),
        end=datetime.fromtimestamp(end, tz=UTC).date().isoformat(),
        runs=len(inside),
        results=sum(int(r.total_tests or 0) for r in inside),
        failed_runs_pct=_pct(failed, len(inside)),
        median_fix_hours=_hours(delivery.median_fix_s),
        red_pct=delivery.red_pct,
    )


def build(
    cycles: Iterable[str], *, since: datetime, days: int | None = None, now: datetime | None = None
) -> CaseStudy:
    """Before/after numbers for *cycles* around *since* (the switch day)."""
    from qasight.history.cases import cases_for_run
    from qasight.history.read_model import list_cycle_runs
    from qasight.repository.db import get_repository
    from qasight.services.run_failures import message_signature
    from qasight.services.similar_causes import evaluate, examples_from_store
    from qasight.services.triage import TriageStore

    names = [c.strip() for c in cycles if c.strip()]
    now = now or datetime.now(tz=UTC)
    since = since if since.tzinfo else since.replace(tzinfo=UTC)
    span = timedelta(days=days) if days else max(now - since, timedelta(days=1))
    runs: list[Any] = []
    for name in names:
        runs.extend(list_cycle_runs(name, limit=5000))
    pivot = since.timestamp()
    before = _period(runs, set(names), pivot - span.total_seconds(), pivot)
    after = _period(runs, set(names), pivot, min(pivot + span.total_seconds(), now.timestamp()))

    imported = 0
    repo = get_repository()
    for run in runs:
        record = repo.get_run(run.run_id)
        if record is not None and (record.metadata_ or {}).get("reportportal_launch_uuid"):
            imported += 1
    causes = [
        t for name in names for t in TriageStore().list(name) if t.marked_at.timestamp() >= pivot
    ]
    # When each marked message first failed, to time how long its cause took to be marked.
    first_seen: dict[tuple[str, str], float] = {}
    wanted = {(c.cycle, c.signature) for c in causes}
    if wanted:
        for run in sorted(runs, key=lambda r: r.started_at):
            for case in cases_for_run(run).values():
                if case.get("status") not in ("failed", "broken"):
                    continue
                key = (run.cycle or "", message_signature(str(case.get("message") or ""))[:1024])
                if key in wanted:
                    first_seen.setdefault(key, run.started_at)
    waits = [
        max(0.0, c.marked_at.timestamp() - first_seen[(c.cycle, c.signature)])
        for c in causes
        if (c.cycle, c.signature) in first_seen
    ]
    accuracy = evaluate([e for e in examples_from_store() if e.cycle in names])
    notes: list[str] = []
    if before.runs == 0:
        notes.append(
            "No runs before the switch: import them first (qasight import reportportal, "
            "qasight ingest --from-github) for a before/after comparison."
        )
    return CaseStudy(
        cycles=names,
        since=since.date().isoformat(),
        before=before,
        after=after,
        causes_marked=len(causes),
        median_hours_to_cause=_hours(statistics.median(waits)) if waits else None,
        suggestion_precision=accuracy.precision,
        suggestion_coverage=accuracy.coverage,
        imported_from_reportportal=imported,
        notes=notes,
    )


def _cell(value: Any, unit: str = "") -> str:
    return "-" if value is None else f"{value}{unit}"


def markdown(study: CaseStudy) -> str:
    b, a = study.before, study.after
    rows = [
        ("Runs recorded", _cell(b.runs), _cell(a.runs)),
        ("Test results", f"{b.results:,}", f"{a.results:,}"),
        ("Runs that failed", _cell(b.failed_runs_pct, "%"), _cell(a.failed_runs_pct, "%")),
        (
            "Median time to fix a broken test",
            _cell(b.median_fix_hours, " h"),
            _cell(a.median_fix_hours, " h"),
        ),
        ("Time the default branch was red", _cell(b.red_pct, "%"), _cell(a.red_pct, "%")),
    ]
    lines = [
        f"## {', '.join(study.cycles)}: before and after {study.since}",
        "",
        f"| | Before ({b.start} to {b.end}) | After ({a.start} to {a.end}) |",
        "| --- | --- | --- |",
        *[f"| {name} | {before} | {after} |" for name, before, after in rows],
        "",
        f"- Failure causes marked since the switch: {study.causes_marked}",
        f"- Median time from a failure showing to its cause being marked: {_cell(study.median_hours_to_cause, ' h')}",
        f"- Suggested causes that were right: {_cell(study.suggestion_precision and round(study.suggestion_precision * 100), '%')}"
        f" (a suggestion for {_cell(study.suggestion_coverage and round(study.suggestion_coverage * 100), '%')} of marked failures)",
    ]
    if study.imported_from_reportportal:
        lines.append(f"- Runs imported from ReportPortal: {study.imported_from_reportportal}")
    for note in study.notes:
        lines.append(f"- Note: {note}")
    return "\n".join(lines) + "\n"
