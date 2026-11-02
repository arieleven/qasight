"""AI settings outside the web UI: ``testo ai setup``, ``testo ai summary`` and CI.

The UI keeps its settings in ``ai_settings.json`` (``TESTO_AI_SETTINGS_PATH``,
:class:`~testo_core.services.ai.integration_settings.FileBackedAiSettingsStore`).
The CLI reads and writes the same file, so a provider picked here shows on the
AI settings page and the other way round. In CI there is usually no file, so
``TESTO_AI_PROVIDER`` (with ``TESTO_AI_MODEL``, ``TESTO_AI_BASE_URL`` and
``TESTO_AI_KEY_ENV``, and ``TESTO_AI_SHARE_TEST_SOURCE=1`` to send the failing
tests' source too) turns summaries on from the environment and wins over it.

API keys are never written anywhere: the settings name the environment variable
that holds the key.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, replace
from typing import cast

from testo_core.services.ai.integration_settings import (
    AiIntegrationSettings,
    FileBackedAiSettingsStore,
    InMemoryAiSettingsStore,
    default_ai_settings_path,
)
from testo_core.services.ai.provider_base import AiProviderName

PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "openai_compatible", "claude_code")

# Google's OpenAI-compatible endpoint: Gemini has a free tier, so a summary costs nothing.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass(frozen=True)
class Detected:
    settings: AiIntegrationSettings
    why: str


def detect_settings(env: dict[str, str] | None = None) -> Detected | None:
    """The provider this machine is already set up for, most specific first."""
    env = dict(os.environ if env is None else env)
    if env.get("OPENAI_API_KEY"):
        return Detected(
            AiIntegrationSettings(enabled=True, provider="openai", model="gpt-4.1-mini"),
            "OPENAI_API_KEY is set",
        )
    if env.get("ANTHROPIC_API_KEY"):
        return Detected(
            AiIntegrationSettings(
                enabled=True, provider="anthropic", model="claude-haiku-4-5", timeout_s=30.0
            ),
            "ANTHROPIC_API_KEY is set",
        )
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if env.get(name):
            return Detected(
                AiIntegrationSettings(
                    enabled=True,
                    provider="openai_compatible",
                    model="gemini-2.5-flash",
                    base_url=GEMINI_BASE_URL,
                    api_key_env_var=name,
                    timeout_s=30.0,
                ),
                f"{name} is set (Gemini's free tier, through its OpenAI-compatible API)",
            )
    if shutil.which(env.get("TESTO_CLAUDE_CLI") or "claude"):
        return Detected(
            AiIntegrationSettings(enabled=True, provider="claude_code", model="", timeout_s=120.0),
            "the Claude Code CLI is installed (uses its own sign-in, no key)",
        )
    return None


def settings_from_env(env: dict[str, str] | None = None) -> AiIntegrationSettings | None:
    """Settings from ``TESTO_AI_PROVIDER`` and friends, or ``None`` when it isn't set."""
    env = dict(os.environ if env is None else env)
    provider = env.get("TESTO_AI_PROVIDER", "").strip().lower()
    if not provider:
        return None
    if provider not in PROVIDERS:
        raise ValueError(
            f"TESTO_AI_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}"
        )
    base = AiIntegrationSettings(enabled=True, provider=cast(AiProviderName, provider))
    timeout = env.get("TESTO_AI_TIMEOUT_S", "").strip()
    return replace(
        base,
        model=env.get("TESTO_AI_MODEL", "").strip() or base.model,
        base_url=env.get("TESTO_AI_BASE_URL", "").strip() or None,
        api_key_env_var=env.get("TESTO_AI_KEY_ENV", "").strip() or None,
        # CI runners are slower than the UI's default allows for a model's first answer.
        timeout_s=float(timeout) if timeout else 60.0,
        share_test_source=env.get("TESTO_AI_SHARE_TEST_SOURCE", "").strip().lower()
        in ("1", "true", "yes", "on"),
    )


def settings_store() -> InMemoryAiSettingsStore:
    """The store the UI uses, with ``TESTO_AI_PROVIDER`` (if set) taking its place."""
    from_env = settings_from_env()
    if from_env is not None:
        store = InMemoryAiSettingsStore()
        store.update(**{k: v for k, v in from_env.__dict__.items()})
        return store
    path = default_ai_settings_path()
    return FileBackedAiSettingsStore(path) if path is not None else InMemoryAiSettingsStore()


def save_settings(settings: AiIntegrationSettings) -> str:
    """Write *settings* where the UI reads them; returns the path."""
    path = default_ai_settings_path()
    if path is None:
        raise RuntimeError("TESTO_AI_SETTINGS_PATH is empty, so settings are not kept")
    settings.to_provider_config().validate()
    store = FileBackedAiSettingsStore(path)
    store.update(**{k: v for k, v in settings.__dict__.items() if k != "runtime_api_key"})
    return str(path)
