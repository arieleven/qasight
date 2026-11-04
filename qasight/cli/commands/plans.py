"""``qasight cycles`` — inspect cycles defined in qasight.yaml."""

from __future__ import annotations

from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(help="Inspect cycles defined in the config.", no_args_is_help=True)


@app.command("list")
def list_plans(
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a qasight.yaml file (defaults to discovery).",
    ),
) -> None:
    """List every cycle defined in the resolved configuration."""
    from rich.table import Table

    from qasight.cli.ui.console import default_console
    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load

    console = default_console()
    try:
        cfg = discover_and_load(config_path=config)
    except ConfigError as exc:
        console.print(f"[fail]config error:[/] {exc}")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc

    table = Table(title="Cycles", show_lines=False, title_justify="left")
    table.add_column("Name", style="title")
    table.add_column("Description")
    table.add_column("Stages", justify="right")
    for cycle in cfg.cycles.values():
        table.add_row(cycle.name, cycle.description or "", str(len(cycle.stages)))
    console.print(table)


@app.command("show")
def show_plan(
    name: str = typer.Argument(..., help="Cycle name to show."),
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a qasight.yaml file (defaults to discovery).",
    ),
) -> None:
    """Pretty-print one resolved cycle."""
    from rich.table import Table

    from qasight.cli.ui.console import default_console
    from qasight.config.errors import ConfigError, PlanNotFoundError
    from qasight.config.loader import discover_and_load
    from qasight.config.resolver import resolve_plan

    console = default_console()
    try:
        cfg = discover_and_load(config_path=config)
        plan = resolve_plan(cfg, plan_name=name)
    except PlanNotFoundError as exc:
        console.print(f"[fail]{exc}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc
    except ConfigError as exc:
        console.print(f"[fail]config error:[/] {exc}")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc

    console.print(f"[title]{plan.name}[/]: {plan.description or ''}")
    table = Table(show_lines=False)
    table.add_column("#", style="muted", justify="right")
    table.add_column("Stage", style="title")
    table.add_column("Equipment", style="framework")
    table.add_column("Target")
    table.add_column("Args")
    table.add_column("Timeout", justify="right")
    for idx, stage in enumerate(plan.stages, start=1):
        table.add_row(
            str(idx),
            stage.name,
            stage.framework,
            str(stage.target_repo),
            " ".join(stage.args) or "",
            f"{stage.timeout_s:.0f}s" if stage.timeout_s else "-",
        )
    console.print(table)
