from __future__ import annotations

import logging

from fastapi import APIRouter, Response

from qasight.repository.db import get_repository
from qasight.repository.db_config import get_engine
from qasight_api.models import HealthLiveResponse, HealthReadyResponse, ReadinessCheck

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("/live", response_model=HealthLiveResponse)
def live() -> HealthLiveResponse:
    return HealthLiveResponse(status="ok")


@router.get("/ready", response_model=HealthReadyResponse)
def ready(response: Response) -> HealthReadyResponse:
    checks: dict[str, ReadinessCheck] = {}

    try:
        engine = get_engine()
        with engine.connect():
            pass
        checks["db"] = ReadinessCheck(status="ok")
    except Exception as exc:  # pragma: no cover - dependency specific
        # Broad on purpose: any failure is reported as "degraded" (503), never a crash.
        logger.debug("readiness: db check failed", exc_info=True)
        checks["db"] = ReadinessCheck(status="degraded", detail=str(exc))

    try:
        _ = get_repository()
        checks["repository"] = ReadinessCheck(status="ok")
    except Exception as exc:  # pragma: no cover - dependency specific
        # Broad on purpose: any failure is reported as "degraded" (503), never a crash.
        logger.debug("readiness: repository check failed", exc_info=True)
        checks["repository"] = ReadinessCheck(status="degraded", detail=str(exc))

    checks["disk"] = _disk_check()

    is_degraded = any(check.status == "degraded" for check in checks.values())
    if is_degraded:
        response.status_code = 503
    return HealthReadyResponse(status="degraded" if is_degraded else "ready", checks=checks)


def _disk_check() -> ReadinessCheck:
    """Free space where the history is kept: a warning when low, degraded when nearly full."""
    from qasight.history.storage import FULL_DISK_BYTES, disk_space, human_bytes, low_disk_pct

    disk = disk_space()
    if disk is None:
        return ReadinessCheck(status="ok", detail="free space unknown")
    detail = f"{human_bytes(disk.free_bytes)} free ({disk.free_pct:.0f}%) at {disk.path}"
    if disk.free_bytes < FULL_DISK_BYTES:
        return ReadinessCheck(status="degraded", detail=detail)
    if disk.free_pct < low_disk_pct():
        return ReadinessCheck(status="warning", detail=detail)
    return ReadinessCheck(status="ok", detail=detail)
