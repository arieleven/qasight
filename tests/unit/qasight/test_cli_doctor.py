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


def test_doctor_without_a_config_is_fine(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero-config use (ingest, ui, run -- CMD) is supported, so no file is INFO, not FAIL."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "Config load" in result.stdout
    assert "INFO" in result.stdout


def test_doctor_named_config_that_is_missing_exits_invalid_input(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = runner.invoke(app, ["doctor", "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code == int(EngineExitCode.INVALID_INPUT)
    assert "FAIL" in result.stdout


def test_doctor_broken_config_exits_invalid_input(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / "qasight.yaml").write_text("version: 1\ncycles: [not, a, map]\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == int(EngineExitCode.INVALID_INPUT)


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


def test_doctor_probes_database_url_without_a_config(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero-config use (``qasight ingest`` / ``qasight ui``) still gets its database checked."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    result = runner.invoke(app, ["doctor"])
    assert "connection probe succeeded" in result.stdout
    assert "no database.url / DATABASE_URL" not in result.stdout
