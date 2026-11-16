"""Failure causes: mark one failure's cause, and later failures with the same message inherit it.

See :mod:`qasight.services.triage`. Marking and clearing are writes, so they
need the API token (or a ``runner`` account) like starting a run does.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from qasight.services.triage import Triage, TriageError, TriageStore
from qasight_api.models import ApiModel, TriageItem, TriageListResponse, TriageRequest
from qasight_api.security import TOKEN_PRINCIPAL, current_principal, require_cycle_role

router = APIRouter(prefix="/api/v1", tags=["triage"])


def triage_item(triage: Triage) -> TriageItem:
    return TriageItem(
        cycle=triage.cycle,
        signature=triage.signature,
        cause=triage.cause,  # type: ignore[arg-type]  # TriageStore only stores CAUSES
        note=triage.note,
        example_message=triage.example_message,
        run_id=triage.run_id,
        test_key=triage.test_key,
        marked_by=triage.marked_by,
        marked_at=triage.marked_at.timestamp(),
    )


@router.get("/triage", response_model=TriageListResponse)
def list_triage(cycle: str | None = None) -> TriageListResponse:
    """Every marked cause, newest first, optionally only one cycle's."""
    return TriageListResponse(causes=[triage_item(t) for t in TriageStore().list(cycle)])


class CauseAccuracyItem(ApiModel):
    cause: str
    suggested: int
    correct: int


class TriageAccuracyResponse(ApiModel):
    # Marked causes replayed, one at a time, against all the others.
    examples: int
    suggested: int
    correct: int
    # correct / suggested; None with no suggestion made.
    precision: float | None
    # suggested / examples; None with no marked cause.
    coverage: float | None
    by_cause: list[CauseAccuracyItem]


@router.get("/triage/accuracy", response_model=TriageAccuracyResponse)
def triage_accuracy(cycle: str | None = None) -> TriageAccuracyResponse:
    """How often a cause suggested from similar marked failures would have been right."""
    from qasight.services.similar_causes import evaluate, examples_from_store

    result = evaluate(examples_from_store(cycle))
    return TriageAccuracyResponse(
        examples=result.examples,
        suggested=result.suggested,
        correct=result.correct,
        precision=result.precision,
        coverage=result.coverage,
        by_cause=[
            CauseAccuracyItem(cause=k, suggested=v[0], correct=v[1])
            for k, v in result.by_cause.items()
        ],
    )


@router.put("/triage", response_model=TriageItem)
def mark_triage(body: TriageRequest, request: Request) -> TriageItem:
    """Set the cause of a failure message in a cycle; replaces an earlier one for the same message."""
    require_cycle_role(request, body.cycle)
    principal = current_principal(request)
    # The shared API token names no one; an account does.
    marked_by = principal.username if principal and principal != TOKEN_PRINCIPAL else None
    try:
        triage = TriageStore().mark(
            cycle=body.cycle,
            message=body.message,
            cause=body.cause,
            note=body.note,
            run_id=body.run_id,
            test_key=body.test_key,
            marked_by=marked_by,
        )
    except TriageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return triage_item(triage)


@router.delete("/triage", status_code=204)
def clear_triage(cycle: str, signature: str, request: Request) -> Response:
    """Forget a cause; failures with that message show no cause again."""
    require_cycle_role(request, cycle)
    if not TriageStore().clear(cycle=cycle, signature=signature):
        raise HTTPException(status_code=404, detail="No cause is marked for that message.")
    return Response(status_code=204)
