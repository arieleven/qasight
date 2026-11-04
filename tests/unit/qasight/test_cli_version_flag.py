"""Regression tests for the top-level ``qasight --version`` / ``-v`` flag."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.cli.commands.version import resolve_version


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_top_level_version_long_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"qasight {resolve_version()}"


def test_top_level_version_short_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["-v"])
    assert result.exit_code == 0
    assert result.output.strip() == f"qasight {resolve_version()}"


def test_version_subcommand_still_works(runner: CliRunner) -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"qasight {resolve_version()}"


def test_subcommands_unaffected_by_global_callback(runner: CliRunner) -> None:
    result = runner.invoke(app, ["cycles", "list", "--help"])
    assert result.exit_code == 0
