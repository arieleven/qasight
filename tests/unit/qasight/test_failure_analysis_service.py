from __future__ import annotations

import pytest

from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services.ai import AiGenerationResult, ProviderUnavailableError
from qasight.services.ai.integration_settings import InMemoryAiSettingsStore
from qasight.services.ai.provider_base import (
    AiGenerationRequest,
    AiProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from qasight.services.failure_analysis_service import FailureAnalysisService


class _FakeProvider:
    provider_name = "openai"
    model = "gpt-4o-mini"

    def __init__(
        self, *, text: str = "The pytest suite failed.", exc: Exception | None = None
    ) -> None:
        self._text = text
        self._exc = exc
        self.requests: list[AiGenerationRequest] = []

    def generate(self, request: AiGenerationRequest) -> AiGenerationResult:
        self.requests.append(request)
        if self._exc is not None:
            raise self._exc
        return AiGenerationResult(text=self._text, provider="openai", model=self.model)


def _failed_run() -> CompletedRunView:
    return CompletedRunView(
        run_id="run-1",
        status=RunStatus.FAILED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind="pytest",
        returncode=1,
        wall_duration_ms=100.0,
        metrics_duration_ms=90,
        total_tests=10,
        passed=8,
        failed=2,
        broken=0,
        skipped=0,
        avg_case_ms=10.0,
        health_pct=80.0,
        target_repo=".",
        snapshot_dir=None,
        audit_json=None,
    )


def _enabled_store() -> InMemoryAiSettingsStore:
    store = InMemoryAiSettingsStore()
    store.update(enabled=True, api_key_source="runtime_input", runtime_api_key="test-token")
    return store


def test_generate_summary_returns_disabled_fallback() -> None:
    store = InMemoryAiSettingsStore()
    state: dict[str, dict] = {"md": {}}
    service = FailureAnalysisService(
        settings_store=store,
        run_lookup=lambda _: _failed_run(),
        metadata_lookup=lambda _: state["md"],
        metadata_upsert=lambda _, patch: state["md"].update(patch) or True,
    )
    summary = service.generate_summary(run_id="run-1")
    assert summary.status == "no_summary_generated"
    assert summary.error_code == "ai_feature_disabled"


def test_force_refresh_failure_preserves_cached_available_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryAiSettingsStore()
    store.update(enabled=True, api_key_source="runtime_input", runtime_api_key="runtime-token")
    cached_summary = {
        "schema_version": "v1",
        "run_id": "run-1",
        "status": "available",
        "summary_text": "Known good root cause.",
        "confidence": "medium",
        "limitations": [],
        "provider": "openai",
        "model": "gpt-4o-mini",
        "generated_at": 1.0,
        "context_stats": {"prompt_chars": 100},
        "error_code": None,
    }
    state: dict[str, dict] = {"md": {"ai_summary_v1": dict(cached_summary)}}

    class _TimeoutProvider:
        def generate(self, request):  # noqa: ANN001,ANN202
            raise ProviderTimeoutError("provider timed out")

    monkeypatch.setattr(
        "qasight.services.failure_analysis_service.build_ai_provider",
        lambda **_: _TimeoutProvider(),
    )
    service = FailureAnalysisService(
        settings_store=store,
        run_lookup=lambda _: _failed_run(),
        metadata_lookup=lambda _: state["md"],
        metadata_upsert=lambda _, patch: state["md"].update(patch) or True,
    )

    result = service.generate_summary(run_id="run-1", force_refresh=True)
    assert result.status == "no_summary_generated"
    assert result.error_code == "provider_timeout"
    assert state["md"]["ai_summary_v1"] == cached_summary


# --- PR #21 tests: provider round-trip, cache reuse, force refresh, error fallbacks ---


def test_generate_summary_persists_provider_result(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _FakeProvider(text="Two assertions failed in checkout tests.")
    state: dict[str, dict] = {
        "md": {
            "error_message": "AssertionError: expected 200 got 500",
            "traceback": "tests/test_checkout.py::test_checkout_total",
        }
    }
    monkeypatch.setattr(
        "qasight.services.failure_analysis_service.build_ai_provider",
        lambda **_: provider,
    )
    service = FailureAnalysisService(
        settings_store=_enabled_store(),
        run_lookup=lambda _: _failed_run(),
        metadata_lookup=lambda _: state["md"],
        metadata_upsert=lambda _, patch: state["md"].update(patch) or True,
    )

    summary = service.generate_summary(run_id="run-1")

    assert summary.status == "available"
    assert summary.summary_text == "Two assertions failed in checkout tests."
    assert summary.provider == "openai"
    assert summary.model == "gpt-4o-mini"
    assert summary.error_code is None
    assert provider.requests[0].max_output_tokens == 300
    stored = state["md"]["ai_summary_v1"]
    assert stored["status"] == "available"
    assert stored["summary_text"] == "Two assertions failed in checkout tests."
    assert stored["context_stats"]["prompt_chars"] > 0


def test_generate_summary_reuses_stored_summary_without_force_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider(text="Fresh summary should not be used.")
    state: dict[str, dict] = {
        "md": {
            "ai_summary_v1": {
                "schema_version": "v1",
                "status": "available",
                "summary_text": "Stored summary",
                "confidence": "medium",
                "limitations": ["log_truncated"],
                "provider": "openai",
                "model": "gpt-4o-mini",
                "generated_at": 123.0,
                "context_stats": {"prompt_chars": 42},
                "error_code": None,
            }
        }
    }
    monkeypatch.setattr(
        "qasight.services.failure_analysis_service.build_ai_provider",
        lambda **_: provider,
    )
    service = FailureAnalysisService(
        settings_store=_enabled_store(),
        run_lookup=lambda _: _failed_run(),
        metadata_lookup=lambda _: state["md"],
        metadata_upsert=lambda _, patch: state["md"].update(patch) or True,
    )

    summary = service.generate_summary(run_id="run-1")

    assert summary.status == "available"
    assert summary.summary_text == "Stored summary"
    assert summary.generated_at == 123.0
    assert summary.limitations == ("log_truncated",)
    assert provider.requests == []


def test_force_refresh_replaces_stored_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _FakeProvider(text="Fresh provider summary")
    state: dict[str, dict] = {
        "md": {
            "ai_summary_v1": {
                "schema_version": "v1",
                "status": "available",
                "summary_text": "Stale summary",
                "confidence": "medium",
                "limitations": [],
                "provider": "openai",
                "model": "gpt-4o-mini",
                "generated_at": 123.0,
                "context_stats": {},
                "error_code": None,
            }
        }
    }
    monkeypatch.setattr(
        "qasight.services.failure_analysis_service.build_ai_provider",
        lambda **_: provider,
    )
    service = FailureAnalysisService(
        settings_store=_enabled_store(),
        run_lookup=lambda _: _failed_run(),
        metadata_lookup=lambda _: state["md"],
        metadata_upsert=lambda _, patch: state["md"].update(patch) or True,
    )

    summary = service.generate_summary(run_id="run-1", force_refresh=True)

    assert summary.status == "available"
    assert summary.summary_text == "Fresh provider summary"
    assert len(provider.requests) == 1
    assert state["md"]["ai_summary_v1"]["summary_text"] == "Fresh provider summary"


@pytest.mark.parametrize(
    ("exc", "error_code", "expected_limitation"),
    [
        (ProviderRateLimitError("quota exhausted"), "provider_rate_limited", None),
        (
            AiProviderError("upstream failed with token=supersecretvalue"),
            "summary_not_available",
            "upstream failed with ***REDACTED***",
        ),
    ],
)
def test_generate_summary_persists_provider_failure_fallback(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    error_code: str,
    expected_limitation: str | None,
) -> None:
    provider = _FakeProvider(exc=exc)
    state: dict[str, dict] = {"md": {"error_message": "failed with token=plaintextsecret"}}
    monkeypatch.setattr(
        "qasight.services.failure_analysis_service.build_ai_provider",
        lambda **_: provider,
    )
    service = FailureAnalysisService(
        settings_store=_enabled_store(),
        run_lookup=lambda _: _failed_run(),
        metadata_lookup=lambda _: state["md"],
        metadata_upsert=lambda _, patch: state["md"].update(patch) or True,
    )

    summary = service.generate_summary(run_id="run-1")

    assert summary.status == "no_summary_generated"
    assert summary.error_code == error_code
    if expected_limitation is not None:
        assert expected_limitation in summary.limitations


# --- PR #24 tests: cache hit/bypass, error redaction, get_summary ---


def test_generate_summary_returns_cached_summary_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, dict] = {
        "md": {
            "ai_summary_v1": {
                "schema_version": "v1",
                "run_id": "run-1",
                "status": "available",
                "summary_text": "Cached root cause",
                "confidence": "high",
                "limitations": ["cached"],
                "provider": "openai",
                "model": "gpt-4o-mini",
                "generated_at": 10.0,
                "context_stats": {"prompt_chars": 12},
                "error_code": None,
            }
        }
    }
    monkeypatch.setattr(
        "qasight.services.failure_analysis_service.build_ai_provider",
        lambda **_: (_ for _ in ()).throw(AssertionError("provider should not be built")),
    )
    service = FailureAnalysisService(
        settings_store=InMemoryAiSettingsStore(),
        run_lookup=lambda _: _failed_run(),
        metadata_lookup=lambda _: state["md"],
        metadata_upsert=lambda _, patch: state["md"].update(patch) or True,
    )

    summary = service.generate_summary(run_id="run-1")

    assert summary.status == "available"
    assert summary.summary_text == "Cached root cause"
    assert summary.limitations == ("cached",)
    assert state["md"]["ai_summary_v1"]["summary_text"] == "Cached root cause"


def test_generate_summary_force_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryAiSettingsStore()
    store.update(
        enabled=True, api_key_source="runtime_input", runtime_api_key="sk-test-runtime-token"
    )
    state: dict[str, dict] = {
        "md": {
            "ai_summary_v1": {
                "schema_version": "v1",
                "run_id": "run-1",
                "status": "available",
                "summary_text": "Cached root cause",
                "confidence": "high",
                "limitations": [],
                "provider": "openai",
                "model": "gpt-4o-mini",
                "generated_at": 10.0,
                "context_stats": {},
                "error_code": None,
            }
        }
    }
    calls: list[str] = []

    class _Provider:
        def generate(self, request):  # noqa: ANN001,ANN202
            calls.append(request.prompt)
            return AiGenerationResult(
                text="Fresh root cause", provider="openai", model="gpt-4o-mini"
            )

    monkeypatch.setattr(
        "qasight.services.failure_analysis_service.build_ai_provider", lambda **_: _Provider()
    )
    service = FailureAnalysisService(
        settings_store=store,
        run_lookup=lambda _: _failed_run(),
        metadata_lookup=lambda _: state["md"],
        metadata_upsert=lambda _, patch: state["md"].update(patch) or True,
    )

    summary = service.generate_summary(run_id="run-1", force_refresh=True)

    assert summary.status == "available"
    assert summary.summary_text == "Fresh root cause"
    assert len(calls) == 1
    assert state["md"]["ai_summary_v1"]["summary_text"] == "Fresh root cause"


def test_generate_summary_provider_error_redacts_exception_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryAiSettingsStore()
    store.update(
        enabled=True, api_key_source="runtime_input", runtime_api_key="sk-test-runtime-token"
    )
    state: dict[str, dict] = {"md": {"error_message": "failed assertion"}}

    class _Provider:
        def generate(self, request):  # noqa: ANN001,ANN202
            raise ProviderUnavailableError("upstream failed with Bearer sk-test-secret-token")

    monkeypatch.setattr(
        "qasight.services.failure_analysis_service.build_ai_provider", lambda **_: _Provider()
    )
    service = FailureAnalysisService(
        settings_store=store,
        run_lookup=lambda _: _failed_run(),
        metadata_lookup=lambda _: state["md"],
        metadata_upsert=lambda _, patch: state["md"].update(patch) or True,
    )

    summary = service.generate_summary(run_id="run-1")

    persisted = state["md"]["ai_summary_v1"]
    assert summary.status == "no_summary_generated"
    assert summary.error_code == "summary_not_available"
    assert "***REDACTED***" in " ".join(summary.limitations)
    assert "sk-test-secret-token" not in " ".join(summary.limitations)
    assert "***REDACTED***" in " ".join(persisted["limitations"])
    assert "sk-test-secret-token" not in " ".join(persisted["limitations"])


def test_get_summary_loads_stored_payload() -> None:
    service = FailureAnalysisService(
        settings_store=InMemoryAiSettingsStore(),
        metadata_lookup=lambda _: {
            "ai_summary_v1": {
                "schema_version": "v1",
                "run_id": "stored-run",
                "status": "available",
                "summary_text": "Stored root cause",
                "confidence": "medium",
                "limitations": ["log_truncated"],
                "provider": "anthropic",
                "model": "claude-3-5-sonnet",
                "generated_at": 42.0,
                "context_stats": {"prompt_chars": 99},
                "error_code": None,
            }
        },
    )

    summary = service.get_summary(run_id="run-1")

    assert summary.run_id == "run-1"
    assert summary.status == "available"
    assert summary.summary_text == "Stored root cause"
    assert summary.limitations == ("log_truncated",)
    assert summary.context_stats == {"prompt_chars": 99}
