from __future__ import annotations

import json
import urllib.error
import urllib.request

from qasight.security.redaction import redact_error_message
from qasight.services.ai import provider_base

from ..provider_base import (
    AiGenerationRequest,
    AiGenerationResult,
    AiProvider,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedProviderModelError,
)


class AnthropicProvider(AiProvider):
    provider_name = "anthropic"

    def __init__(self, *, model: str, api_key: str, timeout_s: float) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout_s = timeout_s

    def generate(self, request: AiGenerationRequest) -> AiGenerationResult:
        payload = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        req = urllib.request.Request(
            url="https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with provider_base.urlopen(req, timeout=self._timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ProviderTimeoutError("Anthropic request timed out.") from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ProviderRateLimitError("Anthropic rate limit exceeded.") from exc
            if exc.code == 400:
                raise UnsupportedProviderModelError(
                    "Anthropic model is unsupported for this key."
                ) from exc
            raise ProviderUnavailableError(f"Anthropic HTTP error {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise ProviderUnavailableError(
                f"Anthropic request failed: {redact_error_message(exc)}"
            ) from exc
        content = body.get("content")
        text = None
        if isinstance(content, list) and content:
            text = content[0].get("text")
        if not text:
            raise ProviderUnavailableError("Anthropic response did not include summary content.")
        usage = body.get("usage", {}) if isinstance(body.get("usage"), dict) else {}
        return AiGenerationResult(
            text=str(text).strip(),
            provider="anthropic",
            model=self.model,
            finish_reason=body.get("stop_reason"),
            usage_input_tokens=usage.get("input_tokens"),
            usage_output_tokens=usage.get("output_tokens"),
        )
