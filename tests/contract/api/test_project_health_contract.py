from __future__ import annotations

from fastapi.testclient import TestClient

from qasight.services.project_health import HealthFinding, HealthSignal, ProjectHealth
from qasight.services.project_review import ProjectReview, SkillResult
from qasight_api.main import create_app


def test_project_health_contract(monkeypatch) -> None:  # noqa: ANN001
    seen: dict[str, object] = {}

    def fake(*, cycle):  # noqa: ANN001, ANN202
        seen["cycle"] = cycle
        return ProjectHealth(
            scope=cycle,
            score=88,
            grade="B",
            cycles=1,
            runs_considered=3,
            tests=10,
            latest_run_at=1.0,
            signals=(HealthSignal("passing", "Passing tests", 90, 30, "9 of 10", "how"),),
            by_cycle=(),
            findings=(HealthFinding("flaky", "web", "test_x", "Flaky", test_key="k"),),
        )

    monkeypatch.setattr("qasight_api.routes.project_health.project_health", fake)
    payload = TestClient(create_app()).get("/api/v1/project-health?cycle=web").json()
    assert seen["cycle"] == "web"
    assert (payload["score"], payload["grade"]) == (88, "B")
    assert payload["signals"][0]["weight"] == 30
    assert payload["findings"][0] == {
        "kind": "flaky",
        "cycle": "web",
        "title": "test_x",
        "detail": "Flaky",
        "test_key": "k",
        "run_id": None,
    }


def test_review_skills_contract() -> None:
    items = TestClient(create_app()).get("/api/v1/project-health/skills").json()["items"]
    assert {"id", "name", "category", "tagline", "summary", "checks", "reads", "default_on"} <= set(
        items[0]
    )
    assert any(not i["default_on"] for i in items)


def test_ai_review_contract(monkeypatch) -> None:  # noqa: ANN001
    from qasight_api.dependencies import get_project_review_service

    calls: list[tuple[object, list[str]]] = []

    class _Service:
        def get_review(self, *, cycle):  # noqa: ANN001, ANN202
            return ProjectReview(scope=cycle, status="not_generated")

        def generate(self, *, cycle, skill_ids):  # noqa: ANN001, ANN202
            calls.append((cycle, skill_ids))
            return ProjectReview(
                scope=cycle,
                status="available",
                score=75,
                skills=(SkillResult("debt", "Test debt", 75, "ok"),),
            )

    app = create_app()
    app.dependency_overrides[get_project_review_service] = _Service
    client = TestClient(app)
    assert client.get("/api/v1/project-health/ai-review").json()["status"] == "not_generated"
    resp = client.post("/api/v1/project-health/ai-review", json={"skills": ["debt"]})
    assert resp.json()["skills"][0]["skill_id"] == "debt"
    assert calls == [(None, ["debt"])]
