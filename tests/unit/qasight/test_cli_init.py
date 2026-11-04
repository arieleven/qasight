"""Tests for ``qasight init``."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.loader import discover_and_load
from qasight.engine.exit_codes import EngineExitCode


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_init_writes_loadable_config(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "qasight.yaml"
    result = runner.invoke(
        app,
        ["init", "--interactive", "--path", str(out)],
        input="\n".join([".", "artifacts", "4", "600", "smoke", ""]) + "\n",
    )
    assert result.exit_code == 0, result.stdout
    assert out.is_file()

    cfg = discover_and_load(config_path=out)
    assert cfg.version == 1
    assert "smoke" in cfg.cycles
    assert cfg.cycles["smoke"].stages[0].framework == "pytest"


def test_init_includes_database_url_when_provided(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "qasight.yaml"
    result = runner.invoke(
        app,
        ["init", "--interactive", "--path", str(out)],
        input="\n".join([".", "artifacts", "4", "600", "nightly", "sqlite:///cli_init.db"]) + "\n",
    )
    assert result.exit_code == 0, result.stdout
    text = out.read_text(encoding="utf-8")
    assert "sqlite:///cli_init.db" in text


def test_init_aborts_without_overwrite(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "qasight.yaml"
    out.write_text("version: 1\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--interactive", "--path", str(out)], input="n\n")
    assert result.exit_code == int(EngineExitCode.INVALID_INPUT)
    assert out.read_text(encoding="utf-8") == "version: 1\n"


def test_init_overwrite_confirmed(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "qasight.yaml"
    out.write_text("version: 1\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["init", "--interactive", "--path", str(out)],
        input="y\n" + "\n".join([".", "artifacts", "4", "600", "smoke", ""]) + "\n",
    )
    assert result.exit_code == 0, result.stdout
    assert "cycles:" in out.read_text(encoding="utf-8")
