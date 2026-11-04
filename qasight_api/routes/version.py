"""``GET /api/v1/version``: the running qasight and, when known, a newer one on PyPI.

Behind the same read protection as runs (not under ``/health/``), so an
unauthenticated visitor can't fingerprint the version. The PyPI check is
:mod:`qasight.services.update_check` (cached a day, off with
``TESTO_NO_UPDATE_CHECK=1``).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from qasight_api.models import VersionResponse

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    from qasight.services.update_check import check

    info = await run_in_threadpool(check, timeout=2.0)
    return VersionResponse(
        version=info.current,
        latest=info.latest,
        update_available=info.available,
    )
