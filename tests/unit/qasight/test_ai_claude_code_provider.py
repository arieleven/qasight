"""The opt-in ``claude_code`` provider: shells out to the local ``claude -p`` (mocked here)."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from qasight.services.ai import AiProviderConfig, build_ai_provider
from qasight.services.ai.provider_base import (
    AiGenerationRequest,
    ProviderMisconfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from qasight.services.ai.providers import claude_code_provider
from qasight.services.ai.providers.claude_code_provider import ClaudeCodeProvider

_MODULE = "qasight.services.ai.providers.claude_code_provider"


@pytest.fixture
def cli_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(claude_code_provider.CLAUDE_CLI_ENV, raising=False)
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/usr/local/bin/{name}")


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    raises: BaseException | None = None,
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen.update(kwargs)
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(f"{_MODULE}.subprocess.run", _run)
    return seen


def _result(text: str, **extra: Any) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 120, "output_tokens": 40},
            **extra,
        }
    )


def _provider(model: str = "sonnet", timeout_s: float = 90) -> ClaudeCodeProvider:
    config = AiProviderConfig(provider="claude_code", model=model, timeout_s=timeout_s)
    provider = build_ai_provider(config=config)
    assert isinstance(provider, ClaudeCodeProvider)
    return provider


def test_needs_no_api_key_or_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for source in ("env", "runtime_input"):
        config = AiProviderConfig(provider="claude_code", model="sonnet", api_key_source=source)  # type: ignore[arg-type]
        config.validate()
        assert config.resolve_api_key() == ""
        assert build_ai_provider(config=config).provider_name == "claude_code"


def test_default_provider_is_unchanged() -> None:
    assert AiProviderConfig().provider == "openai"


@pytest.mark.usefixtures("cli_on_path")
def test_runs_claude_print_with_no_tools_and_prompt_on_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_run(monkeypatch, stdout=_result("  The login test fails on a 500.  "))

    result = _provider().generate(AiGenerationRequest(prompt="SECRET-FREE failure context"))

    assert result.text == "The login test fails on a 500."
    assert result.provider == "claude_code"
    assert result.model == "sonnet"
    assert (result.usage_input_tokens, result.usage_output_tokens) == (120, 40)
    command = seen["command"]
    assert command[0] == "/usr/local/bin/claude"
    assert command[1:4] == ["--print", "--output-format", "json"]
    assert command[command.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in command
    assert command[-2:] == ["--model", "sonnet"]
    # The prompt goes on stdin, never in argv (visible in `ps`), and no shell is used.
    assert "SECRET-FREE failure context" not in " ".join(command)
    assert seen["input"] == "SECRET-FREE failure context"
    assert seen.get("shell", False) is False
    assert seen["timeout"] == 90
    assert seen["cwd"] != "."


@pytest.mark.usefixtures("cli_on_path")
@pytest.mark.parametrize("model", ["", "default"])
def test_empty_model_uses_the_cli_default(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    seen = _fake_run(monkeypatch, stdout=_result("ok"))
    result = _provider(model=model).generate(AiGenerationRequest(prompt="p"))
    assert "--model" not in seen["command"]
    assert result.model in {"default", model}


def test_missing_cli_is_a_clear_misconfiguration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(claude_code_provider.CLAUDE_CLI_ENV, raising=False)
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda _name: None)
    with pytest.raises(ProviderMisconfiguredError, match="Claude Code CLI not found"):
        _provider().generate(AiGenerationRequest(prompt="p"))


def test_cli_path_comes_from_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(claude_code_provider.CLAUDE_CLI_ENV, "/opt/claude/bin/claude")
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: name)
    assert claude_code_provider.resolve_claude_cli() == "/opt/claude/bin/claude"


@pytest.mark.usefixtures("cli_on_path")
def test_not_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(
        monkeypatch,
        stdout=json.dumps(
            {"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}
        ),
        returncode=1,
    )
    with pytest.raises(ProviderMisconfiguredError, match="not logged in"):
        _provider().generate(AiGenerationRequest(prompt="p"))


@pytest.mark.usefixtures("cli_on_path")
def test_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, raises=subprocess.TimeoutExpired(cmd="claude", timeout=5))
    with pytest.raises(ProviderTimeoutError, match="within 5s"):
        _provider(timeout_s=5).generate(AiGenerationRequest(prompt="p"))


@pytest.mark.usefixtures("cli_on_path")
def test_usage_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(
        monkeypatch,
        stdout=json.dumps({"is_error": True, "result": "Claude usage limit reached"}),
        returncode=1,
    )
    with pytest.raises(ProviderRateLimitError):
        _provider().generate(AiGenerationRequest(prompt="p"))


@pytest.mark.usefixtures("cli_on_path")
def test_old_cli_without_the_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, stderr="error: unknown option '--tools'", returncode=1)
    with pytest.raises(ProviderUnavailableError, match="claude update"):
        _provider().generate(AiGenerationRequest(prompt="p"))


@pytest.mark.usefixtures("cli_on_path")
def test_other_failure_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "ghp_" + "a" * 36
    _fake_run(monkeypatch, stderr=f"boom while using {token}", returncode=2)
    with pytest.raises(ProviderUnavailableError, match="exit 2") as info:
        _provider().generate(AiGenerationRequest(prompt="p"))
    assert token not in str(info.value)


@pytest.mark.usefixtures("cli_on_path")
def test_empty_or_garbled_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, stdout="")
    with pytest.raises(ProviderUnavailableError, match="no summary"):
        _provider().generate(AiGenerationRequest(prompt="p"))


@pytest.mark.usefixtures("cli_on_path")
def test_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, raises=PermissionError("permission denied"))
    with pytest.raises(ProviderUnavailableError, match="Could not start Claude Code"):
        _provider().generate(AiGenerationRequest(prompt="p"))
