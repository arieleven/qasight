"""``qasight runs``: list, show and delete recorded runs.

Runs had no command of their own: they were listed by ``report list`` (report
archives, with other ids), compared by ``diff`` and deleted by ``clean --history
--run``. ``qasight runs`` works on the run history, with the ids ``qasight run``,
the UI and MCP show, and takes the first characters of an id.
"""

from __future__ import annotations

import json

import typer

from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(
    help="Recorded runs: list them, show one (failures first) or delete some.",
    invoke_without_command=True,
)

_JSON_HELP = "Print as JSON (on by itself for coding agents)."


def _fail(message: str, *, machine: bool) -> None:
    if machine:
        typer.echo(json.dumps({"error": message}), err=True)
    else:
        typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))


@app.callback()
def _default(
    ctx: typer.Context,
    cycle: str | None = typer.Option(None, "--cycle", help="Only this cycle's runs."),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=100, help="How many runs."),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """``qasight runs`` alone lists the newest runs."""
    if ctx.invoked_subcommand is None:
        list_cmd(cycle=cycle, limit=limit, as_json=as_json)


@app.command("list")
def list_cmd(
    cycle: str | None = typer.Option(None, "--cycle", help="Only this cycle's runs."),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=100, help="How many runs."),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """The newest runs, newest first."""
    from qasight.cli.output_mode import wants_json
    from qasight.mcp_server import HistoryTools

    data = HistoryTools().list_runs(cycle=cycle, limit=limit)
    if wants_json(as_json):
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    runs = data["runs"]
    if not runs:
        typer.echo("No runs recorded yet. Record one with `qasight run -- <test command>`.")
        return
    from rich.table import Table

    from qasight.cli.ui.console import default_console

    table = Table(show_lines=False)
    for column, justify in (
        ("Run", "left"),
        ("Cycle", "left"),
        ("Outcome", "left"),
        ("Tests", "right"),
        ("Failed", "right"),
        ("When", "left"),
        ("Branch", "left"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]
    for r in runs:
        style = "ok" if r["outcome"] == "passed" else "fail"
        failed = (r.get("failed") or 0) + (r.get("broken") or 0)
        table.add_row(
            r["run_id"][:8],
            r.get("cycle") or "",
            f"[{style}]{r['outcome']}[/]",
            str(r.get("tests") or 0),
            str(failed),
            (r.get("created_at") or "").replace("T", " ")[:16],
            r.get("branch") or "",
        )
    default_console().print(table)


@app.command("show")
def show_cmd(
    run: str = typer.Argument("latest", help="Run id, its first characters, or latest."),
    cycle: str | None = typer.Option(None, "--cycle", help="With latest: this cycle's."),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """One run: outcome, counts, stages and its failing tests (new breakages first)."""
    from qasight.cli.output_mode import wants_json
    from qasight.cli.run_ref import RunRefError, resolve_run
    from qasight.mcp_server import HistoryTools

    machine = wants_json(as_json)
    try:
        found = resolve_run(run, cycle=cycle)
    except RunRefError as exc:
        _fail(f"{exc}.", machine=machine)
        return
    data = HistoryTools().get_run(found.run_id)
    if machine:
        from qasight.services.run_news import run_news

        news = run_news(found.run_id)
        data["news"] = news.to_dict() if news is not None else None
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    from rich.markup import escape

    from qasight.cli.ui.console import default_console
    from qasight.services.run_news import render_lines, run_news

    console = default_console()
    summary = data
    console.print(
        f"[title]Run {found.run_id}[/]  cycle {escape(str(found.cycle))}  "
        f"{escape(str(summary.get('created_at') or ''))}"
    )
    news = run_news(found.run_id)
    if news is not None:
        for line in render_lines(news)[:-1]:
            console.print(escape(line), highlight=False)
    for command in (data.get("failures") or {}).get("rerun_commands") or []:
        console.print(f"[muted]Rerun:[/] {escape(str(command))}", highlight=False)


@app.command("delete")
def delete_cmd(
    runs: list[str] = typer.Argument(..., help="Run ids (or their first characters)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Really delete."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only say what would go."),
) -> None:
    """Delete runs from the history, with their per-test rows and report copies."""
    from qasight.cli.commands.clean import _delete_chosen_runs
    from qasight.cli.run_ref import RunRefError, resolve_run

    ids: list[str] = []
    for ref in runs:
        try:
            ids.append(resolve_run(ref).run_id)
        except RunRefError:
            ids.append(ref)  # reported as "No run ..." below, like clean --run
    _delete_chosen_runs(ids, yes=yes, dry_run=dry_run)
