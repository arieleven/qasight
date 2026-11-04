"""Ask about your tests: ``POST /api/v1/ask`` answers a question from the run history."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from qasight.services.ask_tests import AskTestsService, AskTurn
from qasight_api.dependencies import get_ask_tests_service
from qasight_api.models import AskRequest, AskResponse

router = APIRouter(prefix="/api/v1", tags=["ai"])


@router.post("/ask", response_model=AskResponse)
def ask_about_tests(
    payload: AskRequest,
    service: AskTestsService = Depends(get_ask_tests_service),
) -> AskResponse:
    """One answer from the configured AI provider; nothing is stored."""
    answer = service.ask(
        payload.question,
        cycle=payload.cycle or None,
        history=[AskTurn(question=t.question, answer=t.answer) for t in payload.history],
    )
    return AskResponse.model_validate(answer.to_dict())
