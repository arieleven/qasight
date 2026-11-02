"""History retention: delete the runs ``history.retention`` says have expired.

A run that expires loses its ``runrecord`` row, its ``test_result`` rows and its
report copy under ``static/history/<run_id>/``, together, oldest first. Runs still
``RUNNING`` are never touched. Report archives (``testo report list``) are a
separate store with their own ids and are not affected.

Used by ``testo clean --history`` and by the API server on a timer
(:func:`testo_api.main._lifespan`). Without a ``history:`` section in
``testosterone.yaml`` nothing is ever deleted by age or count.
:func:`delete_runs` deletes runs a person picked the same way (``testo clean
--history --run``, ``POST /api/v1/runs:delete``, the Runs page).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from testo_core import paths
from testo_core.config.history_section import HistoryRetention
from testo_core.repository.db import get_repository, get_test_result_store
from testo_core.repository.models import RunStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PruneReport:
    """What a prune did (or, with ``dry_run``, would do)."""

    run_ids: tuple[str, ...] = ()
    runs_deleted: int = 0
    test_rows_deleted: int = 0
    snapshots_deleted: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def runs_selected(self) -> int:
        return len(self.run_ids)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def expired_run_ids(
    keys: list[tuple[str, str | None, datetime | None, RunStatus]],
    retention: HistoryRetention,
    *,
    now: datetime | None = None,
) -> list[str]:
    """The ids in ``keys`` (newest first) that break ``retention``; oldest first.

    ``days`` expires runs that started before the cutoff; ``runs_per_cycle`` expires
    each cycle's runs beyond its newest N (runs without a cycle form their own group).
    A ``RUNNING`` run never expires. A run with no start time counts as oldest.
    """
    if not retention.enabled:
        return []
    moment = now or datetime.now(tz=UTC)
    cutoff = moment - timedelta(days=retention.days) if retention.days is not None else None
    expired: list[tuple[datetime, str]] = []
    seen_per_cycle: dict[str | None, int] = {}
    ordered = sorted(
        keys, key=lambda k: _aware(k[2]) if k[2] is not None else datetime.min.replace(tzinfo=UTC)
    )
    ordered.reverse()  # newest first, whatever order the repository returned
    for run_id, cycle, start_time, status in ordered:
        if status == RunStatus.RUNNING:
            continue
        started = _aware(start_time) if start_time is not None else datetime.min.replace(tzinfo=UTC)
        seen_per_cycle[cycle] = seen_per_cycle.get(cycle, 0) + 1
        too_old = cutoff is not None and started < cutoff
        too_many = retention.runs_per_cycle is not None and (
            seen_per_cycle[cycle] > retention.runs_per_cycle
        )
        if too_old or too_many:
            expired.append((started, run_id))
    expired.sort()
    return [run_id for _, run_id in expired]


def _snapshot_dir(run_id: str) -> Path | None:
    """``static/history/<run_id>/`` when it exists and really is under the history root."""
    try:
        folder = paths.safe_child_path(paths.STATIC_HISTORY_ROOT, run_id, label="run id")
    except ValueError:
        return None
    return folder if folder.is_dir() else None


def prune_history(retention: HistoryRetention, *, dry_run: bool = False) -> PruneReport:
    """Delete every run that breaks ``retention`` (rows, test rows and snapshot folder).

    With ``dry_run`` nothing is deleted; the report says what would be. Rows go
    first, so a run never outlives its files, and a folder that cannot be removed is
    reported, not raised.
    """
    repo = get_repository()
    run_ids = expired_run_ids(repo.list_run_keys(), retention)
    if not run_ids or dry_run:
        return PruneReport(run_ids=tuple(run_ids))
    report = _remove(run_ids)
    logger.info(
        "history retention deleted %s run(s), %s test row(s), %s snapshot(s)",
        report.runs_deleted,
        report.test_rows_deleted,
        report.snapshots_deleted,
    )
    return report


@dataclass(frozen=True)
class DeleteReport(PruneReport):
    """What :func:`delete_runs` did, plus the ids it left alone."""

    skipped_running: tuple[str, ...] = ()
    not_found: tuple[str, ...] = ()


def delete_runs(run_ids: list[str], *, dry_run: bool = False) -> DeleteReport:
    """Delete these runs (rows, test rows and snapshot folder), as retention would.

    The ``testo clean --history --run`` and ``POST /api/v1/runs:delete`` path: the
    person picked the runs, so no retention rule is needed. A run still ``RUNNING``
    is skipped, and so is an id the history doesn't have.
    """
    wanted = list(dict.fromkeys(r.strip() for r in run_ids if r and r.strip()))
    status_by_id = {run_id: status for run_id, _c, _s, status in get_repository().list_run_keys()}
    chosen = [r for r in wanted if r in status_by_id and status_by_id[r] != RunStatus.RUNNING]
    running = tuple(r for r in wanted if status_by_id.get(r) == RunStatus.RUNNING)
    missing = tuple(r for r in wanted if r not in status_by_id)
    if not chosen or dry_run:
        return DeleteReport(run_ids=tuple(chosen), skipped_running=running, not_found=missing)
    done = _remove(chosen)
    logger.info("deleted %s chosen run(s)", done.runs_deleted)
    return DeleteReport(
        run_ids=done.run_ids,
        runs_deleted=done.runs_deleted,
        test_rows_deleted=done.test_rows_deleted,
        snapshots_deleted=done.snapshots_deleted,
        errors=done.errors,
        skipped_running=running,
        not_found=missing,
    )


def _remove(run_ids: list[str]) -> PruneReport:
    """Delete the rows, then the snapshot folders; a folder that won't go is reported."""
    test_rows = get_test_result_store().delete_runs(run_ids)
    runs = get_repository().delete_runs(run_ids)
    snapshots = 0
    errors: list[str] = []
    for run_id in run_ids:
        folder = _snapshot_dir(run_id)
        if folder is None:
            continue
        try:
            shutil.rmtree(folder)
            snapshots += 1
        except OSError as exc:
            errors.append(f"{folder}: {exc}")
    if errors:
        logger.warning("could not remove %s run history folder(s)", len(errors))
    return PruneReport(
        run_ids=tuple(run_ids),
        runs_deleted=runs,
        test_rows_deleted=test_rows,
        snapshots_deleted=snapshots,
        errors=tuple(errors),
    )
