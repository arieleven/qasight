"""Quarantine kept on the server: list, add and release tests whose failures don't fail a run.

See :mod:`qasight.services.quarantine_store`. Adding and releasing are writes,
so they need the API token (or a ``runner`` account) like marking a failure's
cause does, and land in the audit log in accounts mode.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from qasight.services.quarantine_store import (
    QuarantineEntry,
    QuarantineError,
    QuarantineStore,
    release_after,
)
from qasight_api.models import QuarantineItem, QuarantineListResponse, QuarantineRequest
from qasight_api.security import TOKEN_PRINCIPAL, current_principal

router = APIRouter(prefix="/api/v1", tags=["quarantine"])


def quarantine_item(entry: QuarantineEntry) -> QuarantineItem:
    return QuarantineItem(
        cycle=entry.cycle,
        test=entry.test,
        is_glob=entry.is_glob,
        reason=entry.reason,
        owner=entry.owner,
        added_by=entry.added_by,
        added_at=entry.added_at.timestamp(),
    )


@router.get("/quarantine", response_model=QuarantineListResponse)
def list_quarantine(cycle: str | None = None) -> QuarantineListResponse:
    """Every quarantined test kept on the server, newest first, optionally one cycle's.

    ``qasight.yaml`` rules aren't listed here; runs apply both.
    """
    return QuarantineListResponse(
        items=[quarantine_item(e) for e in QuarantineStore().list(cycle)],
        release_after=release_after(),
    )


@router.put("/quarantine", response_model=QuarantineItem)
def add_quarantine(body: QuarantineRequest, request: Request) -> QuarantineItem:
    """Quarantine a test (exact name or glob) in a cycle; replaces an earlier entry for it."""
    principal = current_principal(request)
    # The shared API token names no one; an account does.
    added_by = principal.username if principal and principal != TOKEN_PRINCIPAL else None
    try:
        entry = QuarantineStore().add(
            cycle=body.cycle,
            test=body.test,
            reason=body.reason,
            owner=body.owner,
            added_by=added_by,
        )
    except QuarantineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return quarantine_item(entry)


@router.delete("/quarantine", status_code=204)
def remove_quarantine(cycle: str, test: str) -> Response:
    """Release a test: its failures fail runs again."""
    if not QuarantineStore().remove(cycle=cycle, test=test):
        raise HTTPException(status_code=404, detail="That test isn't quarantined in this cycle.")
    return Response(status_code=204)
