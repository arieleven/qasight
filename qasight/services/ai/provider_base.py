from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import IO, Literal, Protocol


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn a redirect into an ``HTTPError``: following one would resend the
    provider's ``Authorization`` / ``x-api-key`` header to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN201, ARG002, PLR0913
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urlopen(req: urllib.request.Request, timeout: float) -> IO[bytes]:
    """Send a provider request without following redirects (see ``_NoRedirect``)."""
    return _OPENER.open(req, timeout=timeout)  # type: ignore[no-any-return]  # nosec: B310


AiProviderName = Literal["openai", "anthropic", "openai_compatible", "claude_code"]


@dataclass(frozen=True)
class AiGenerationRequest:
    prompt: str
    max_output_tokens: int = 300
    temperature: float = 0.1


@dataclass(frozen=True)
class AiGenerationResult:
    text: str
    provider: AiProviderName
    model: str
    finish_reason: str | None = None
    usage_input_tokens: int | None = None
    usage_output_tokens: int | None = None


class AiProviderError(RuntimeError):
    pass


class ProviderMisconfiguredError(AiProviderError):
    pass


class ProviderTimeoutError(AiProviderError):
    pass


class ProviderRateLimitError(AiProviderError):
    pass


class UnsupportedProviderModelError(AiProviderError):
    pass


class ProviderUnavailableError(AiProviderError):
    pass


class AiProvider(Protocol):
    provider_name: AiProviderName
    model: str

    def generate(self, request: AiGenerationRequest) -> AiGenerationResult: ...
