"""Tests for ``qasight doctor``."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.engine.exit_codes import EngineExitCode

_MINIMAL_CFG = (
    "version: 1\n"
    "defaults: {target_repo: ., artifacts_root: artifacts}\n"
    "cycles:\n  c:\n    stages:\n      - {name: s, equipment: pytest, args: ['-q']}\n"
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_doctor_missing_config_exits_invalid_input(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == int(EngineExitCode.INVALID_INPUT)
    assert "Config load" in result.stdout


def test_doctor_passes_when_config_and_executables_present(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(_MINIMAL_CFG, encoding="utf-8")

    # ``pytest`` is guaranteed on PATH inside this test process.
    result = runner.invoke(app, ["doctor", "--config", str(cfg)])
    assert result.exit_code == 0, result.stdout
    assert "Doctor checks passed" in result.stdout
    assert "Database" in result.stdout  # SKIP row present when no DB configured


def test_doctor_missing_executable_exits_invalid_input(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(_MINIMAL_CFG, encoding="utf-8")

    from qasight.cli.commands import doctor as doctor_mod

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _exe: None)
    result = runner.invoke(app, ["doctor", "--config", str(cfg)])
    assert result.exit_code == int(EngineExitCode.INVALID_INPUT)
    assert "not found on PATH" in result.stdout


def test_doctor_database_probe_failure_is_hard_fail(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(_MINIMAL_CFG, encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////nonexistent-dir-xyz/db.sqlite3")

    result = runner.invoke(app, ["doctor", "--config", str(cfg)])
    assert result.exit_code == int(EngineExitCode.INVALID_INPUT)
    assert "Database" in result.stdout
