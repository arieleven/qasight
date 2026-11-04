"""``qasight plugins`` — list the frameworks and reporters this install can use.

Built-ins come first, then packages that registered ``qasight.frameworks`` or
``qasight.reporters`` entry points (:mod:`qasight.plugins`). ``--check``
imports every plugin and exits 1 if one fails, which is the quick test a
plugin author wants after ``pip install -e .``.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(invoke_without_command=True)


@app.callback()
def plugins_cmd(
    ctx: typer.Context,
    check: bool = typer.Option(
        False, "--check", help="Import every plugin and verify its interface; exit 1 on failure."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead of a table."),
) -> None:
    """List the built-in and installed frameworks and reporters (`qasight plugins new` starts one)."""
    if ctx.invoked_subcommand is not None:
        return
    from rich.console import Console
    from rich.table import Table

    from qasight import plugins
    from qasight.config.schema import SUPPORTED_FRAMEWORKS, SUPPORTED_REPORTER_TYPES

    rows: list[dict[str, str | None]] = []
    for kind, names in (
        ("framework", SUPPORTED_FRAMEWORKS),
        ("reporter", SUPPORTED_REPORTER_TYPES),
    ):
        rows += [
            {"kind": kind, "name": n, "target": None, "package": "built in", "status": "ok"}
            for n in sorted(names)
        ]

    failed = 0
    for info in plugins.list_plugins():
        status: str | None = None
        if check:
            try:
                if info.kind == "framework":
                    plugins.load_framework(info.name)
                else:
                    plugins.load_reporter(info.name)
                status = "ok"
            except plugins.PluginError as exc:
                status = f"error: {exc}"
                failed += 1
        rows.append(
            {
                "kind": info.kind,
                "name": info.name,
                "target": info.target,
                "package": info.package,
                "status": status,
            }
        )

    if as_json:
        typer.echo(json.dumps(rows, indent=2))
    else:
        table = Table(title="Frameworks and reporters")
        for col in ("Kind", "Name", "From", "Entry point", *(["Status"] if check else [])):
            table.add_column(col)
        for r in rows:
            cells = [r["kind"] or "", r["name"] or "", r["package"] or "?", r["target"] or ""]
            if check:
                cells.append(r["status"] or "")
            table.add_row(*cells)
        Console().print(table)
        if not plugins.list_plugins():
            typer.echo(
                "No plugins installed. A package adds one with a 'qasight.frameworks' or "
                "'qasight.reporters' entry point; see the Command Reference."
            )
    if failed:
        raise typer.Exit(code=1)


@app.command("new")
def new_cmd(
    name: str = typer.Argument(..., help="The plugin's name, used as `equipment: NAME`."),
    directory: Path = typer.Option(Path("."), "--dir", help="Folder to create the package in."),
) -> None:
    """Write a working plugin package (a framework adapter, a reporter, a test) to start from."""
    from qasight.engine.exit_codes import EngineExitCode
    from qasight.services.plugin_scaffold import ScaffoldError, write_plugin

    try:
        root, names = write_plugin(name, directory.expanduser().resolve())
    except ScaffoldError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT)) from None
    typer.echo(f"Wrote {root}")
    typer.echo(
        f"  equipment: {names.name}  ({names.module}.{names.cls}Adapter)\n"
        f"  reporter:  {names.name}-summary  ({names.module}.{names.cls}SummaryReporter)"
    )
    typer.echo("Next:")
    typer.echo(
        f"  cd {root} && pip install -e . && qasight plugins --check && qasight run --cycle unit"
    )
