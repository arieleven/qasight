"""A mistyped command or option prints a message, not a traceback."""

from __future__ import annotations

import pytest

from qasight.cli.app import main


def test_unknown_command_prints_hint_and_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["runn"]) == 2
    err = capsys.readouterr().err
    assert "No such command" in err
    assert "Traceback" not in err


def test_unknown_option_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--no-such-flag"]) == 2
    err = capsys.readouterr().err
    assert "No such option" in err
    assert "Traceback" not in err


def test_bare_testo_shows_help_without_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Usage" in output
    assert "Traceback" not in output
    assert "NoArgsIsHelpError" not in output
