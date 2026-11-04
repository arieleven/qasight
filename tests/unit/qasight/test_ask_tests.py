"""Ask about your tests (qasight/services/ask_tests.py and ``POST /api/v1/ask``)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qasight.services import ask_tests
from qasight.services.ai import AiGenerationResult
from qasight.services.ai.integration_settings import InMemoryAiSettingsStore
from qasight.services.ai.provider_base import AiGenerationRequest, ProviderTimeoutError
from qasight.services.project_health import ProjectHealth
from qasight_api.dependencies import get_ask_tests_service
from qasight_api.main import create_app

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


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


def _health(cycles: int) -> ProjectHealth:
    return ProjectHealth(
        scope=None,
        score=90,
        grade="A",
        cycles=cycles,
        runs_considered=2,
        tests=10,
        latest_run_at=2.0,
        signals=(),
        by_cycle=(),
        findings=(),
    )


@pytest.fixture
def evidence(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    scopes: list[str | None] = []

    def fake_health(*, cycle: str | None) -> ProjectHealth:
        scopes.append(cycle)
        return _health(1)

    monkeypatch.setattr(ask_tests, "project_health", fake_health)
    monkeypatch.setattr(
        ask_tests,
        "history_evidence",
        lambda health, *, max_chars: (
            '{"cycles": [{"cycle": "web", "flaky": ["test_pay"]}]}',
            {"cycles": 1},
        ),
    )
    return scopes


def _store(enabled: bool = True) -> InMemoryAiSettingsStore:
    store = InMemoryAiSettingsStore()
    store.update(enabled=enabled, api_key_source="runtime_input", runtime_api_key="k")
    return store


def test_answers_from_the_evidence_with_recent_turns(evidence, monkeypatch) -> None:  # noqa: ANN001
    provider = _FakeProvider("  test_pay is flaky: it failed 2 of 10 runs.\n")
    monkeypatch.setattr(ask_tests, "build_ai_provider", lambda **_: provider)
    history = [ask_tests.AskTurn(f"q{i}", f"a{i}") for i in range(6)]

    answer = ask_tests.AskTestsService(settings_store=_store()).ask(
        "  Which tests are flaky?  ", cycle="web", history=history
    )

    assert answer.status == "answered"
    assert answer.answer == "test_pay is flaky: it failed 2 of 10 runs."
    assert (answer.question, answer.scope, answer.model) == (
        "Which tests are flaky?",
        "web",
        "gemini",
    )
    assert answer.context_stats == {"cycles": 1}
    assert evidence == ["web"]
    prompt = provider.requests[0].prompt
    assert '"flaky": ["test_pay"]' in prompt
    assert "never source code" in prompt
    assert prompt.rstrip().endswith("Question: Which tests are flaky?\nAnswer:")
    # Only the last MAX_TURNS turns go back to the model.
    assert "Q: q1\n" not in prompt and "Q: q2\nA: a2" in prompt and "Q: q5\nA: a5" in prompt
    assert provider.requests[0].max_output_tokens >= 700


def test_failures_say_why(evidence, monkeypatch) -> None:  # noqa: ANN001
    off = ask_tests.AskTestsService(settings_store=_store(enabled=False))
    assert off.ask("anything?").error_code == "ai_feature_disabled"
    service = ask_tests.AskTestsService(settings_store=_store())
    assert service.ask("   ").error_code == "empty_question"

    monkeypatch.setattr(ask_tests, "project_health", lambda *, cycle: _health(0))
    assert service.ask("anything?").error_code == "no_runs"

    monkeypatch.setattr(ask_tests, "project_health", lambda *, cycle: _health(1))
    monkeypatch.setattr(
        ask_tests, "build_ai_provider", lambda **_: _FakeProvider(exc=ProviderTimeoutError("x"))
    )
    timed_out = service.ask("anything?")
    assert (timed_out.status, timed_out.error_code) == ("failed", "provider_timeout")
    monkeypatch.setattr(ask_tests, "build_ai_provider", lambda **_: _FakeProvider("  "))
    assert service.ask("anything?").error_code == "empty_reply"


def test_api_route(evidence, monkeypatch) -> None:  # noqa: ANN001
    provider = _FakeProvider("web is red: test_checkout broke.")
    monkeypatch.setattr(ask_tests, "build_ai_provider", lambda **_: provider)
    app = create_app()
    app.dependency_overrides[get_ask_tests_service] = lambda: ask_tests.AskTestsService(
        settings_store=_store()
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask",
        json={"question": "What broke?", "history": [{"question": "hi", "answer": "hello"}]},
    )
    assert response.status_code == 200
    assert response.json()["answer"] == "web is red: test_checkout broke."
    assert evidence == [None]
    assert "Q: hi\nA: hello" in provider.requests[0].prompt
    assert client.post("/api/v1/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/v1/ask", json={"question": "x" * 501}).status_code == 422
