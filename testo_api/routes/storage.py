"""``GET /api/v1/storage``: how much the run history takes and how full the disk is.

Walking ``static/history/`` can take a while on a big server, so the answer is
cached for a few minutes (``?refresh=1`` measures again). See
:mod:`testo_core.history.storage`.
"""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from testo_api.models import StorageDisk, StorageResponse

router = APIRouter(prefix="/api/v1", tags=["history"])

CACHE_S = 300.0
_lock = threading.Lock()
_cached: tuple[float, StorageResponse] | None = None


def _measure() -> StorageResponse:
    from testo_core.history.storage import low_disk_pct, storage_summary

    summary = storage_summary()
    disk = None
    if summary.disk is not None:
        disk = StorageDisk(
            path=summary.disk.path,
            total_bytes=summary.disk.total_bytes,
            free_bytes=summary.disk.free_bytes,
            free_pct=round(summary.disk.free_pct, 1),
            low=summary.disk.free_pct < low_disk_pct(),
        )
    return StorageResponse(
        runs=summary.runs,
        report_archives=summary.report_archives,
        report_archive_bytes=summary.report_archive_bytes,
        history_files_bytes=summary.history_files_bytes,
        database_bytes=summary.database_bytes,
        database_dialect=summary.database_dialect,
        disk=disk,
        measured_at=time.time(),
    )


def storage_now(*, refresh: bool = False) -> StorageResponse:
    global _cached
    with _lock:
        if not refresh and _cached is not None and time.monotonic() - _cached[0] < CACHE_S:
            return _cached[1]
        result = _measure()
        _cached = (time.monotonic(), result)
        return result


def forget_cached_storage() -> None:
    """Measure again on the next call (after a delete or prune, and in tests)."""
    global _cached
    with _lock:
        _cached = None


@router.get("/storage", response_model=StorageResponse)
async def storage(
    refresh: bool = Query(False, description="Measure again instead of using the cached answer."),
) -> StorageResponse:
    return await run_in_threadpool(storage_now, refresh=refresh)
