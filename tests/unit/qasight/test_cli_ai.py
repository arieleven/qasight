"""``qasight ai`` and ``qasight report markdown --ai`` (G2, P1 of the new-user journey audit)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.services import ai_run_summary
from qasight.services.ai.cli_settings import detect_settings, settings_from_env
from qasight.services.run_summary_markdown import ai_section


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "ai_settings.json"
    monkeypatch.setenv("TESTO_AI_SETTINGS_PATH", str(path))
    for key in (
        "TESTO_AI_PROVIDER",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TESTO_CLAUDE_CLI", "no-such-claude-cli")
    return path


def test_detect_prefers_openai_then_anthropic_then_gemini() -> None:
    env = {"TESTO_CLAUDE_CLI": "no-such-claude-cli"}
    assert detect_settings(env) is None
    gemini = detect_settings({**env, "GEMINI_API_KEY": "k"})
    assert gemini is not None and gemini.settings.provider == "openai_compatible"
    assert gemini.settings.api_key_env_var == "GEMINI_API_KEY"
    both = detect_settings({**env, "GEMINI_API_KEY": "k", "ANTHROPIC_API_KEY": "k"})
    assert both is not None and both.settings.provider == "anthropic"
    first = detect_settings({**env, "ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k"})
    assert first is not None and first.settings.provider == "openai"


def test_env_settings_win_and_bad_provider_is_named() -> None:
    assert settings_from_env({}) is None
    s = settings_from_env({"TESTO_AI_PROVIDER": "Anthropic", "TESTO_AI_MODEL": "m"})
    assert s is not None and (s.provider, s.model, s.timeout_s) == ("anthropic", "m", 60.0)
    with pytest.raises(ValueError, match="TESTO_AI_PROVIDER"):
        settings_from_env({"TESTO_AI_PROVIDER": "skynet"})


def test_setup_saves_the_detected_provider_without_the_key(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")

    result = CliRunner().invoke(app, ["ai", "setup"])

    assert result.exit_code == 0, result.output
    saved = json.loads(settings_file.read_text())
    assert saved["provider"] == "openai_compatible" and saved["enabled"] is True
    assert saved["api_key_env_var"] == "GEMINI_API_KEY"
    assert "secret-key" not in settings_file.read_text()

    status = CliRunner().invoke(app, ["ai", "status"])
    assert "openai_compatible" in status.output and "$GEMINI_API_KEY (set)" in status.output


def test_setup_with_nothing_found_exits_2(settings_file: Path) -> None:
    result = CliRunner().invoke(app, ["ai", "setup"])
    assert result.exit_code == 2
    assert not settings_file.exists()


def test_status_when_off(settings_file: Path) -> None:
    result = CliRunner().invoke(app, ["ai", "status"])
    assert result.exit_code == 0
    assert "qasight ai setup" in result.output


def test_summary_prints_the_text_or_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ai_run_summary,
        "summarize",
        lambda **_: ai_run_summary.RunAiSummary("r1", "Login broke."),
    )
    ok = CliRunner().invoke(app, ["ai", "summary"])
    assert ok.exit_code == 0 and "Login broke." in ok.output

    monkeypatch.setattr(
        ai_run_summary,
        "summarize",
        lambda **_: ai_run_summary.RunAiSummary(None, None, "No failed run to summarize."),
    )
    none = CliRunner().invoke(app, ["ai", "summary"])
    assert none.exit_code == 2


def test_ai_section_quotes_the_summary_before_the_footer() -> None:
    markdown = "<!-- testo-summary:c -->\n### Run\n\n<sub>footer</sub>\n"

    out = ai_section(markdown, "Fix <script> for @team\n\nthen rerun")

    assert out.splitlines()[-1] == "<sub>footer</sub>"
    assert "> Fix &lt;script&gt; for &#64;team" in out
    assert "\n>\n> then rerun" in out
    assert "_No AI summary: off._" in ai_section(markdown, None, "off")
