"""``qasight help`` — every command, not only the core ten ``qasight --help`` shows.

``qasight --help`` lists the commands every user needs. The rest work exactly the
same and are listed here: ``--all`` prints them grouped, ``--json`` prints every
command, subcommand, alias and exit code for coding agents.
"""

from __future__ import annotations

import json
from typing import Any

import typer

EXIT_CODES = {
    "0": "success (or skipped by a trigger)",
    "1": "tests failed",
    "2": "invalid input",
    "3": "infrastructure failure",
    "4": "internal error",
}


def _subcommands(command: Any) -> list[dict[str, Any]]:
    children = getattr(command, "commands", None) or {}
    out = []
    for name, child in sorted(children.items()):
        if getattr(child, "hidden", False):
            continue
        first_line = (child.help or "").strip().splitlines()
        entry: dict[str, Any] = {"name": name, "help": first_line[0] if first_line else ""}
        nested = _subcommands(child)
        if nested:
            entry["subcommands"] = nested
        out.append(entry)
    return out


def describe() -> dict[str, Any]:
    """Every top-level command as plain data (what ``--json`` prints)."""
    from typer.main import get_command

    from qasight.cli.app import COMMANDS, CORE_PANELS, MORE_PANELS, TAGLINE, app
    from qasight.cli.commands.version import resolve_version

    group = get_command(app)
    commands = []
    aliases = {}
    for spec in COMMANDS:
        if spec.alias_of:
            aliases[spec.name] = spec.alias_of
            continue
        entry: dict[str, Any] = {
            "name": spec.name,
            "help": spec.help,
            "group": spec.panel,
            "core": spec.core,
        }
        subs = _subcommands(group.commands[spec.name])  # type: ignore[attr-defined]
        if subs:
            entry["subcommands"] = subs
        commands.append(entry)
    return {
        "name": "qasight",
        "version": resolve_version(),
        "summary": TAGLINE,
        "groups": list(CORE_PANELS) + list(MORE_PANELS),
        "commands": commands,
        "aliases": aliases,
        "exit_codes": EXIT_CODES,
        "machine_output": "qasight run --ci streams NDJSON events; plan_finished is last.",
    }


def help_command(
    show_all: bool = typer.Option(False, "--all", "-a", help="List every command, grouped."),
    as_json: bool = typer.Option(
        False, "--json", help="Every command, subcommand, alias and exit code as JSON."
    ),
) -> None:
    """All commands (--all), or every command as JSON for coding agents (--json)."""
    if as_json:
        typer.echo(json.dumps(describe(), indent=2))
        return
    from typer.main import get_command

    from qasight.cli.app import COMMANDS, app

    group = get_command(app)
    if show_all:
        # Every command visible except aliases, in COMMANDS order so the panels read
        # Start, Record, Understand, Share, then the "More" groups.
        commands = group.commands  # type: ignore[attr-defined]
        aliases = {spec.name for spec in COMMANDS if spec.alias_of} | {"help"}
        ordered = {spec.name: commands[spec.name] for spec in COMMANDS}
        ordered.update({name: cmd for name, cmd in commands.items() if name not in ordered})
        for name, command in ordered.items():
            command.hidden = name in aliases
        group.commands = ordered  # type: ignore[attr-defined]
    ctx = group.make_context("qasight", [], resilient_parsing=True)
    if show_all:
        old = ", ".join(spec.name for spec in COMMANDS if spec.alias_of)
        ctx.command.epilog = f"Old names ({old}) keep working."
    typer.echo(group.get_help(ctx), nl=False)
