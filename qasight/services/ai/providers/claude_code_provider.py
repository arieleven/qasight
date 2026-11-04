"""Summaries from the locally installed Claude Code CLI (``claude -p``), with no API key.

Runs ``claude`` as a subprocess, so it uses whatever login that CLI already has on
this machine (a Claude subscription or the CLI's own key). Meant for local runs: a
team server usually has no ``claude`` on its ``PATH``, and the call fails with a
clear error there.

The prompt contains test output, which is untrusted text, so the CLI is started with
no tools and no MCP servers (it can only answer, not read files or run commands), in
an empty temporary directory, with the prompt on stdin (never in ``argv``, where
``ps`` would show it). The command is never built from the settings page: the binary
is ``claude`` on ``PATH``, or the path in ``TESTO_CLAUDE_CLI`` set by whoever starts
the API.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec: B404 - fixed argv, no shell
import tempfile

from qasight.security.redaction import redact_text

from ..provider_base import (
    AiGenerationRequest,
    AiGenerationResult,
    AiProvider,
    AiProviderName,
    ProviderMisconfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

# Path to the ``claude`` binary when it is not on PATH. Read from the environment only,
# so the settings page (an HTTP request) can never choose which program the API runs.
CLAUDE_CLI_ENV = "TESTO_CLAUDE_CLI"
DEFAULT_CLAUDE_CLI = "claude"

_NOT_LOGGED_IN_HINTS = (
    "login",
    "log in",
    "logged in",
    "invalid api key",
    "authenticat",
    "oauth",
    "credit balance",
)
_RATE_LIMIT_HINTS = ("rate limit", "usage limit", "limit reached", "overloaded", "429")
_UNKNOWN_FLAG_HINTS = ("unknown option", "unknown argument", "error: option")


def resolve_claude_cli() -> str | None:
    """Absolute path of the Claude Code CLI, or ``None`` when it is not installed."""
    configured = os.getenv(CLAUDE_CLI_ENV, "").strip()
    return shutil.which(os.path.expanduser(configured or DEFAULT_CLAUDE_CLI))


def _short(text: str) -> str:
    return redact_text(" ".join(text.split()))[:300].rstrip(".")


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


class ClaudeCodeProvider(AiProvider):
    provider_name: AiProviderName = "claude_code"

    def __init__(self, *, model: str, timeout_s: float) -> None:
        # An empty model (or "default") lets the CLI pick its own default model.
        self.model = model.strip()
        self._timeout_s = timeout_s

    def build_command(self, executable: str) -> list[str]:
        command = [
            executable,
            "--print",
            "--output-format",
            "json",
            # No tools at all: the prompt holds test output, which must not be able to
            # make the CLI read files, run commands or fetch URLs.
            "--tools",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]
        if self.model and self.model.lower() != "default":
            command += ["--model", self.model]
        return command

    def generate(self, request: AiGenerationRequest) -> AiGenerationResult:
        executable = resolve_claude_cli()
        if executable is None:
            raise ProviderMisconfiguredError(
                "Claude Code CLI not found: install it (npm install -g @anthropic-ai/claude-code), "
                f"run `claude` once to log in, or set {CLAUDE_CLI_ENV} to its path."
            )
        command = self.build_command(executable)
        try:
            with tempfile.TemporaryDirectory(prefix="testo-claude-") as workdir:
                completed = subprocess.run(  # nosec: B603 - fixed argv, shell=False
                    command,
                    input=request.prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self._timeout_s,
                    cwd=workdir,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeoutError(
                f"Claude Code did not answer within {self._timeout_s:g}s; "
                "raise the timeout on the AI settings page."
            ) from exc
        except OSError as exc:
            raise ProviderUnavailableError(
                f"Could not start Claude Code: {_short(str(exc))}"
            ) from exc
        return self._parse(completed)

    def _parse(self, completed: subprocess.CompletedProcess[str]) -> AiGenerationResult:
        body: object = None
        try:
            body = json.loads(completed.stdout) if completed.stdout.strip() else None
        except ValueError:
            body = None
        payload = body if isinstance(body, dict) else {}
        result_text = str(payload.get("result") or "").strip()
        failed = completed.returncode != 0 or bool(payload.get("is_error")) or not result_text
        if failed:
            self._raise_for_failure(completed, payload, result_text)
        raw_usage = payload.get("usage")
        usage: dict[str, object] = raw_usage if isinstance(raw_usage, dict) else {}
        return AiGenerationResult(
            text=result_text,
            provider=self.provider_name,
            model=self.model or "default",
            finish_reason=str(payload.get("stop_reason") or payload.get("subtype") or "") or None,
            usage_input_tokens=_int_or_none(usage.get("input_tokens")),
            usage_output_tokens=_int_or_none(usage.get("output_tokens")),
        )

    def _raise_for_failure(
        self,
        completed: subprocess.CompletedProcess[str],
        payload: dict[str, object],
        result_text: str,
    ) -> None:
        detail = result_text or completed.stderr.strip() or completed.stdout.strip()
        lowered = detail.lower()
        if any(hint in lowered for hint in _NOT_LOGGED_IN_HINTS):
            raise ProviderMisconfiguredError(
                "Claude Code is not logged in: run `claude` once in a terminal and sign in "
                f"({_short(detail)})."
            )
        if any(hint in lowered for hint in _RATE_LIMIT_HINTS):
            raise ProviderRateLimitError(f"Claude Code hit a usage limit: {_short(detail)}.")
        if any(hint in lowered for hint in _UNKNOWN_FLAG_HINTS):
            raise ProviderUnavailableError(
                f"This Claude Code version is too old for QASight: {_short(detail)}. "
                "Update it with `claude update`."
            )
        if detail:
            raise ProviderUnavailableError(
                f"Claude Code failed (exit {completed.returncode}): {_short(detail)}."
            )
        raise ProviderUnavailableError(
            f"Claude Code returned no summary (exit {completed.returncode})."
        )
