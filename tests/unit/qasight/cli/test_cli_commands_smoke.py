"""Cheap smoke tests for the non-run CLI surface (QA Strategies layout table)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from tests.fixtures.engine import write_minimal_config

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def test_help_lists_core_commands(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "cycles", "config", "report", "version"):
        assert command in result.output


def test_version_command(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip()


def test_run_help_documents_implemented_flags(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    plain_output = _ANSI_ESCAPE.sub("", result.output)
    for flag in ("--cycle", "--config", "--stream", "--ci", "--workers", "--force"):
        assert flag in plain_output
    # Roadmap flags must not be advertised until they exist.
    for flag in ("--tag", "--fail-fast", "--dry-run", "--reporter"):
        assert flag not in plain_output


def test_cycles_list_shows_configured_cycles(cli_runner: CliRunner, tmp_path: Path) -> None:
    cfg = write_minimal_config(tmp_path, cycle="listed-cycle")
    result = cli_runner.invoke(app, ["cycles", "list", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "listed-cycle" in result.output


def test_cycles_show_prints_stages(cli_runner: CliRunner, tmp_path: Path) -> None:
    # Keep names short: Rich wraps long cell values across table lines.
    cfg = write_minimal_config(tmp_path, cycle="shown")
    result = cli_runner.invoke(app, ["cycles", "show", "shown", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "shown-stage" in result.output


def test_cycles_show_unknown_cycle_exits_2(cli_runner: CliRunner, tmp_path: Path) -> None:
    cfg = write_minimal_config(tmp_path)
    result = cli_runner.invoke(app, ["cycles", "show", "ghost", "--config", str(cfg)])
    assert result.exit_code == 2
