"""Regression test: `qasight --help` grouping must match the documented panels."""

from __future__ import annotations

from typer.testing import CliRunner

from qasight.cli.app import app

runner = CliRunner()


def test_help_output_is_grouped_into_documented_panels() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0

    for panel in ("Run and report", "Config", "Diagnostics", "Maintenance", "About"):
        assert panel in result.output, f"missing panel: {panel}"


def test_help_output_is_not_a_flat_command_list() -> None:
    result = runner.invoke(app, ["--help"])
    assert "Commands ─" not in result.output
