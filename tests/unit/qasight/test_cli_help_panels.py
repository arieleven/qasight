"""Regression test: `qasight --help` shows the core commands; `qasight help --all` shows the rest."""

from __future__ import annotations

import json
import re

from typer.testing import CliRunner

from qasight.cli.app import COMMANDS, CORE_PANELS, MORE_PANELS, TAGLINE, app

runner = CliRunner()

# CI forces colour (FORCE_COLOR), so help text arrives with ANSI escapes.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _help(*argv: str) -> str:
    result = runner.invoke(app, list(argv), env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return _ANSI.sub("", result.output)


CORE = ("init", "demo", "doctor", "run", "ingest", "summary", "flaky", "ui", "ci", "mcp")


def test_help_leads_with_the_promise_and_the_core_panels() -> None:
    output = _help("--help")
    assert TAGLINE in output
    for panel in CORE_PANELS:
        assert f"─ {panel} ─" in output, f"missing panel: {panel}"
    assert "More:" not in output
    assert "qasight help --all" in output


def test_help_lists_exactly_the_core_commands() -> None:
    assert tuple(spec.name for spec in COMMANDS if spec.core) == CORE
    output = _help("--help")
    for name in CORE:
        assert f"│ {name} " in output
    for name in ("scorecard", "lease", "export", "version", "pool", "try"):
        assert f"│ {name} " not in output


def test_help_all_lists_every_command_but_not_aliases() -> None:
    output = _help("help", "--all")
    for panel in CORE_PANELS + MORE_PANELS:
        assert f"─ {panel} ─" in output, f"missing panel: {panel}"
    for spec in COMMANDS:
        shown = f"│ {spec.name} " in output
        assert shown == (spec.alias_of is None), spec.name
    assert "Commands ─" not in output


def test_help_json_describes_every_command_for_agents() -> None:
    result = runner.invoke(app, ["help", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["summary"] == TAGLINE
    names = {c["name"] for c in data["commands"]}
    assert set(CORE) <= names and {"scorecard", "lease", "export"} <= names
    assert data["aliases"] == {
        "cycles": "doctor",
        "diff": "summary --against",
        "export-site": "export site",
        "badge": "export badge",
        "plans": "cycles",
        "try": "ci preview",
        "pool": "lease pools",
        "config-db": "config db",
        "version": "--version",
    }
    assert set(data["exit_codes"]) == {"0", "1", "2", "3", "4"}
    ci = next(c for c in data["commands"] if c["name"] == "ci")
    assert {"preview", "publish"} <= {s["name"] for s in ci["subcommands"]}


def test_hidden_commands_and_aliases_still_work() -> None:
    for argv in (
        ["scorecard", "--help"],
        ["ci", "preview", "--help"],
        ["try", "--help"],
        ["lease", "pools", "--help"],
        ["pool", "--help"],
        ["config", "db", "--help"],
        ["config-db", "--help"],
        ["version"],
    ):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, (argv, result.output)
