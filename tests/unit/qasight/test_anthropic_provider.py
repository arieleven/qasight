"""AnthropicProvider.generate: request shape, response parsing and error mapping."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from qasight.services.ai.provider_base import (
    AiGenerationRequest,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedProviderModelError,
)
from qasight.services.ai.providers.anthropic_provider import AnthropicProvider


class _Response:
    def __init__(self, body: dict) -> None:
        self._body = body

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


def _provider() -> AnthropicProvider:
    return AnthropicProvider(model="claude-test", api_key="sk-test", timeout_s=5)


def test_generate_sends_the_prompt_and_parses_text_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def _urlopen(req, timeout):  # noqa: ANN001,ANN202
        seen["url"] = req.full_url
        seen["key"] = req.get_header("X-api-key")
        seen["timeout"] = timeout
        seen["payload"] = json.loads(req.data)
        return _Response(
            {
                "content": [{"type": "text", "text": "  What failed: x  "}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 4},
            }
        )

    monkeypatch.setattr("qasight.services.ai.provider_base.urlopen", _urlopen)
    result = _provider().generate(AiGenerationRequest(prompt="why?", max_output_tokens=50))

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["key"] == "sk-test"
    assert seen["timeout"] == 5
    assert seen["payload"] == {
        "model": "claude-test",
        "max_tokens": 50,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": "why?"}],
    }
    assert result.text == "What failed: x"
    assert result.provider == "anthropic"
    assert result.finish_reason == "end_turn"
    assert (result.usage_input_tokens, result.usage_output_tokens) == (12, 4)


@pytest.mark.parametrize("body", [{"content": []}, {"content": [{"text": ""}]}, {}])
def test_empty_content_is_unavailable(monkeypatch: pytest.MonkeyPatch, body: dict) -> None:
    monkeypatch.setattr(
        "qasight.services.ai.provider_base.urlopen", lambda req, timeout: _Response(body)
    )  # noqa: ARG005
    with pytest.raises(ProviderUnavailableError, match="did not include summary content"):
        _provider().generate(AiGenerationRequest(prompt="x"))


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (429, ProviderRateLimitError),
        (400, UnsupportedProviderModelError),
        (500, ProviderUnavailableError),
    ],
)
def test_http_errors_map_to_provider_errors(
    monkeypatch: pytest.MonkeyPatch, code: int, error: type[Exception]
) -> None:
    def _urlopen(req, timeout):  # noqa: ANN001,ANN202, ARG001
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr("qasight.services.ai.provider_base.urlopen", _urlopen)
    with pytest.raises(error):
        _provider().generate(AiGenerationRequest(prompt="x"))


def test_timeout_and_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _timeout(req, timeout):  # noqa: ANN001,ANN202, ARG001
        raise TimeoutError

    monkeypatch.setattr("qasight.services.ai.provider_base.urlopen", _timeout)
    with pytest.raises(ProviderTimeoutError):
        _provider().generate(AiGenerationRequest(prompt="x"))

    def _offline(req, timeout):  # noqa: ANN001,ANN202, ARG001
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr("qasight.services.ai.provider_base.urlopen", _offline)
    with pytest.raises(ProviderUnavailableError, match="Anthropic request failed"):
        _provider().generate(AiGenerationRequest(prompt="x"))
