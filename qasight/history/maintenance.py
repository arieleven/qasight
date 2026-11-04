"""The few writes the read side needs: metadata patches, orphaned-run cleanup and
recording runs that died before they could record themselves."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qasight.engine.cycle_lock import LOCK_NAME, RunMarker, take_unfinished
from qasight.engine.exit_codes import EngineExitCode
from qasight.repository.db import get_repository
from qasight.repository.models import RunRecord, RunStatus

logger = logging.getLogger(__name__)


def upsert_run_metadata(*, run_id: str, metadata_patch: dict[str, Any]) -> bool:
    """Shallow-merge ``metadata_patch`` into the run's metadata; ``False`` if the run is missing."""
    return get_repository().merge_run_metadata(run_id, metadata_patch)


def cleanup_orphaned_runs(*, note: str = "Orphaned due to system crash") -> int:
    """Mark runs still ``RUNNING`` as ``FAILED``; returns how many were updated.

    Called when the API starts: a run cannot still be executing if the process
    that ran it is gone (reload, crash, reboot), and leaving it ``RUNNING``
    would show it as live in the UI forever.
    """
    repo = get_repository()
    orphans = repo.list_runs_by_status(RunStatus.RUNNING)
    if not orphans:
        return 0
    now = datetime.now(tz=UTC)
    updated: list[RunRecord] = []
    for record in orphans:
        metadata = dict(record.metadata_ or {})
        metadata.setdefault("error", "orphaned")
        metadata.setdefault("error_message", note)
        metadata.setdefault("orphaned_at", time.time())
        record.metadata_ = metadata
        record.status = RunStatus.FAILED
        record.end_time = now
        updated.append(record)
    count = repo.bulk_update(updated)
    if count:
        logger.warning("Marked %s orphaned RUNNING run(s) as FAILED (%s).", count, note)
    return count


INTERRUPTED_ERROR = "interrupted"


def record_unfinished_run(marker: RunMarker) -> str | None:
    """Record a run that died holding its cycle lock as ``FAILED`` (no test results).

    Without this the run would leave no trace at all: runs are written to the history
    when they finish. Returns the new run id, or None when it could not be written.
    """
    started = datetime.fromtimestamp(marker.started_at, tz=UTC)
    now = datetime.now(tz=UTC)
    message = (
        f"This run stopped without finishing: process {marker.pid} on {marker.host} ended "
        "before it could record results (killed, out of memory, or the machine or server "
        "went down). Its test results were not saved."
    )
    try:
        record = get_repository().create_run(
            status=RunStatus.FAILED,
            start_time=started,
            end_time=now,
            metadata={
                "plan": marker.cycle,
                "test_kind": "cycle",
                "source": "engine",
                "exit_code": int(EngineExitCode.INFRA_FAILURE),
                "returncode": int(EngineExitCode.INFRA_FAILURE),
                "started_at": marker.started_at,
                "finished_at": now.timestamp(),
                "started_at_iso": started.isoformat(),
                "stage_count": 0,
                "stages": [],
                "error": INTERRUPTED_ERROR,
                "error_message": message,
                "interrupted_host": marker.host,
                "interrupted_pid": marker.pid,
            },
        )
    except Exception:
        # Broad on purpose: best-effort, like every other history write.
        logger.warning("could not record the unfinished run of %s", marker.cycle, exc_info=True)
        return None
    logger.warning(
        "Recorded an unfinished run of cycle %r (pid %s on %s) as failed.",
        marker.cycle,
        marker.pid,
        marker.host,
    )
    return str(record.id)


def recover_unfinished_runs(artifacts_root: Path) -> int:
    """Record every run under *artifacts_root* that died holding its cycle lock.

    For the API at startup: the run it was executing when it crashed is otherwise
    only noticed when that cycle runs again. Returns how many were recorded.
    """
    root = artifacts_root.expanduser()
    if not root.is_dir():
        return 0
    recorded = 0
    for lock in sorted(root.glob(f"*/{LOCK_NAME}")):
        marker = take_unfinished(root, lock.parent.name)
        if marker is not None and record_unfinished_run(marker):
            recorded += 1
    return recorded
