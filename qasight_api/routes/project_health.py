from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from qasight.services.project_health import project_health
from qasight.services.project_review import SKILLS, ProjectReviewService
from qasight_api.dependencies import get_project_review_service
from qasight_api.models import (
    GenerateProjectReviewRequest,
    ProjectHealthResponse,
    ProjectReviewResponse,
    ReviewSkillItem,
    ReviewSkillsResponse,
)

router = APIRouter(prefix="/api/v1", tags=["project-health"])


@router.get("/project-health", response_model=ProjectHealthResponse)
def get_project_health(cycle: str | None = None) -> ProjectHealthResponse:
    """The project under test's test health score: every cycle, or ``cycle`` alone."""
    return ProjectHealthResponse.model_validate(asdict(project_health(cycle=cycle or None)))


@router.get("/project-health/skills", response_model=ReviewSkillsResponse)
def list_review_skills() -> ReviewSkillsResponse:
    """The review skills the AI review can use, each with what it checks and reads."""
    return ReviewSkillsResponse(
        items=[
            ReviewSkillItem(
                id=s.id,
                name=s.name,
                category=s.category,
                tagline=s.tagline,
                summary=s.summary,
                checks=list(s.checks),
                reads=list(s.reads),
                default_on=s.default_on,
            )
            for s in SKILLS
        ]
    )


@router.get("/project-health/ai-review", response_model=ProjectReviewResponse)
def get_project_review(
    cycle: str | None = None,
    service: ProjectReviewService = Depends(get_project_review_service),
) -> ProjectReviewResponse:
    """The last AI review of the scope, if any; ``stale`` when newer runs came in."""
    return ProjectReviewResponse.model_validate(service.get_review(cycle=cycle or None).to_dict())


@router.post("/project-health/ai-review", response_model=ProjectReviewResponse)
def generate_project_review(
    payload: GenerateProjectReviewRequest,
    service: ProjectReviewService = Depends(get_project_review_service),
) -> ProjectReviewResponse:
    """Ask the configured AI provider to review the project with the chosen skills."""
    review = service.generate(cycle=payload.cycle or None, skill_ids=payload.skills)
    return ProjectReviewResponse.model_validate(review.to_dict())
