"""The AI review of the project's tests (qasight/services/project_review.py)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from qasight.services import project_review
from qasight.services.ai import AiGenerationResult
from qasight.services.ai.integration_settings import InMemoryAiSettingsStore
from qasight.services.ai.provider_base import AiGenerationRequest, ProviderRateLimitError
from qasight.services.project_health import ProjectHealth
from tests._history_fakes import cycle_runs_from


class _Run:
    def __init__(self, run_id: str, cycle: str = "web") -> None:
        self.run_id = run_id
        self.cycle = cycle


class _FakeProvider:
    def __init__(self, text: str = "", exc: Exception | None = None) -> None:
        self.text = text
        self.exc = exc
        self.requests: list[AiGenerationRequest] = []

    def generate(self, request: AiGenerationRequest) -> AiGenerationResult:
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return AiGenerationResult(text=self.text, provider="openai_compatible", model="gemini")


def _reply(**scores: int | None) -> str:
    return (
        "```json\n"
        + json.dumps(
            {
                "summary": "Mostly green.",
                "skills": [
                    {
                        "id": sid,
                        "score": score,
                        "summary": f"{sid} ok",
                        "findings": [{"severity": "urgent", "title": "T", "detail": "D"}],
                    }
                    for sid, score in scores.items()
                ],
            }
        )
        + "\n```"
    )


@pytest.fixture
def world(monkeypatch):  # noqa: ANN001, ANN201
    metadata: dict[str, dict[str, Any]] = {"run-2": {}, "run-1": {}}
    runs = [_Run("run-2"), _Run("run-1")]
    monkeypatch.setattr(project_review, "list_cycle_runs", cycle_runs_from(runs))
    monkeypatch.setattr(project_review, "list_recent_runs", lambda *, limit: runs)
    monkeypatch.setattr(project_review, "get_run_metadata", lambda *, run_id: metadata[run_id])
    monkeypatch.setattr(
        project_review,
        "upsert_run_metadata",
        lambda *, run_id, metadata_patch: metadata[run_id].update(metadata_patch) or True,
    )
    monkeypatch.setattr(
        project_review,
        "project_health",
        lambda *, cycle: ProjectHealth(
            scope=cycle,
            score=90,
            grade="A",
            cycles=1,
            runs_considered=2,
            tests=10,
            latest_run_at=2.0,
            signals=(),
            by_cycle=(),
            findings=(),
        ),
    )
    return metadata


def _store(enabled: bool = True) -> InMemoryAiSettingsStore:
    store = InMemoryAiSettingsStore()
    store.update(enabled=enabled, api_key_source="runtime_input", runtime_api_key="k")
    return store


def test_review_scores_each_enabled_skill_and_is_stored(world, monkeypatch) -> None:  # noqa: ANN001
    provider = _FakeProvider(_reply(reliability=80, strategy=None, debt=60))
    monkeypatch.setattr(project_review, "build_ai_provider", lambda **_: provider)
    service = project_review.ProjectReviewService(settings_store=_store())

    review = service.generate(cycle=None, skill_ids=["reliability", "strategy", "debt", "nope"])

    assert review.status == "available"
    assert [s.skill_id for s in review.skills] == ["reliability", "strategy", "debt"]
    # Skills without a score don't count toward the average.
    assert (review.score, review.grade) == (70, "C")
    assert review.skills[0].findings[0].severity == "medium"
    prompt = provider.requests[0].prompt
    assert 'id "reliability"' in prompt and 'id "naming"' not in prompt
    assert provider.requests[0].max_output_tokens >= 300 + 3 * 450
    assert world["run-2"][project_review.METADATA_KEY]["*"]["score"] == 70

    stored = service.get_review(cycle=None)
    assert (stored.status, stored.score, stored.stale) == ("available", 70, False)
    assert service.get_review(cycle="web").status == "not_generated"


def test_stored_review_on_an_older_run_is_stale(world) -> None:  # noqa: ANN001
    world["run-1"][project_review.METADATA_KEY] = {
        "web": {"scope": "web", "status": "available", "score": 50, "skills": []}
    }
    review = project_review.ProjectReviewService(settings_store=_store()).get_review(cycle="web")
    assert (review.score, review.stale) == (50, True)


def test_failures_say_why(world, monkeypatch) -> None:  # noqa: ANN001
    off = project_review.ProjectReviewService(settings_store=_store(enabled=False))
    assert off.generate(cycle=None, skill_ids=["debt"]).error_code == "ai_feature_disabled"
    service = project_review.ProjectReviewService(settings_store=_store())
    assert service.generate(cycle=None, skill_ids=[]).error_code == "no_skills"

    monkeypatch.setattr(
        project_review, "build_ai_provider", lambda **_: _FakeProvider("Sorry, I can't.")
    )
    assert service.generate(cycle=None, skill_ids=["debt"]).error_code == "unparseable_reply"
    monkeypatch.setattr(
        project_review,
        "build_ai_provider",
        lambda **_: _FakeProvider(exc=ProviderRateLimitError("slow down")),
    )
    assert service.generate(cycle=None, skill_ids=["debt"]).error_code == "provider_rate_limited"
    assert not world["run-2"]


def test_catalog_is_about_tests_only() -> None:
    ids = [s.id for s in project_review.SKILLS]
    assert len(ids) == len(set(ids))
    assert {"architecture", "accessibility", "release"}.isdisjoint(ids)
    for skill in project_review.SKILLS:
        assert skill.summary and skill.checks and skill.reads and skill.rubric
