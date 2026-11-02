"""Queries over stored runs, used by the API routes and the analytics services.

Every read goes through :func:`testo_core.repository.db.get_repository`, so the read side
works the same on SQLite, Postgres and MySQL and never touches SQL directly.
"""

from __future__ import annotations

from typing import Any

from testo_core.history.report_links import local_report_links
from testo_core.history.views import CompletedRunView, RunSessionView, view_from_record
from testo_core.repository.db import get_repository

# Most runs of one cycle any analytics asks for; a per-cycle bound, not a bound on the
# whole table, so a busy cycle no longer hides a quiet one's history.
CYCLE_HISTORY_LIMIT = 500


def list_recent_runs(*, limit: int = 30) -> list[CompletedRunView]:
    """Up to ``limit`` runs, newest first."""
    views = (view_from_record(r) for r in get_repository().list_recent_runs(limit=limit))
    return [v for v in views if v is not None]


def list_cycle_runs(
    cycle: str, *, limit: int = CYCLE_HISTORY_LIMIT, before_run_id: str | None = None
) -> list[CompletedRunView]:
    """``cycle``'s runs, newest first; with ``before_run_id`` only the runs recorded before that run.

    Filtered in SQL on the ``cycle`` and ``start_time`` columns, so it costs the same
    whatever the other cycles are doing. An unknown ``before_run_id`` gives no runs.
    """
    repo = get_repository()
    before = None
    if before_run_id is not None:
        anchor = repo.get_run(before_run_id)
        if anchor is None or anchor.start_time is None:
            return []
        before = anchor.start_time
    records = repo.list_runs(cycle=cycle, before=before, limit=limit)
    views = (view_from_record(r) for r in records)
    return [v for v in views if v is not None]


def latest_run_per_cycle() -> dict[str, CompletedRunView]:
    """The newest run of every cycle, newest cycle first (one query, every cycle).

    Cycles recorded by ``testo ingest`` or another server exist only here, not in
    ``testosterone.yaml``.
    """
    latest: dict[str, CompletedRunView] = {}
    for record in get_repository().latest_run_per_cycle():
        run = view_from_record(record)
        if run is not None and run.cycle and run.cycle not in latest:
            latest[run.cycle] = run
    return latest


def get_run(*, run_id: str) -> CompletedRunView | None:
    """One run by its id, or ``None``."""
    record = get_repository().get_run(run_id)
    return view_from_record(record) if record is not None else None


def get_run_metadata(*, run_id: str) -> dict[str, Any] | None:
    """A copy of the run's raw metadata (failure context, stored AI summary, …), or ``None``."""
    record = get_repository().get_run(run_id)
    return dict(record.metadata_ or {}) if record is not None else None


def list_run_sessions(*, limit: int = 30, cycle: str | None = None) -> list[RunSessionView]:
    """Recent runs with the report links available for each, optionally only ``cycle``'s."""
    runs = list_cycle_runs(cycle, limit=max(1, limit)) if cycle else list_recent_runs(limit=limit)
    return [
        RunSessionView(
            run_id=r.run_id,
            created_at=r.created_at,
            returncode=r.returncode,
            cycle=r.cycle,
            health_pct=r.health_pct,
            total_tests=r.total_tests,
            passed=r.passed,
            failed=r.failed,
            skipped=r.skipped,
            broken=r.broken,
            status=r.status,
            links_under_static=local_report_links(r.run_id),
            branch=r.branch,
            commit=r.commit,
            environment=r.environment,
            release=r.release,
        )
        for r in runs
    ]
