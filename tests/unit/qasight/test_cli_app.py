from __future__ import annotations

import re

from typer.testing import CliRunner

from qasight.cli.app import app

runner = CliRunner()

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def test_shell_completion_enabled() -> None:
    assert app._add_completion is True


def test_help_exposes_completion_options() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    plain_output = _ANSI_ESCAPE.sub("", result.output)
    assert "--install-completion" in plain_output
    assert "--show-completion" in plain_output
