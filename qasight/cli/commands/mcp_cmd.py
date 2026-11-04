"""``qasight mcp`` — an MCP server over the run history, for coding agents (stdio).

``qasight mcp install`` adds it to Claude Code, Cursor, VS Code or Claude Desktop
(:mod:`qasight.services.mcp_install`).
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode
from qasight.paths import CONFIG_FILE_NAMES

MISSING_EXTRA = "The MCP server needs the mcp extra: pip install 'qasight[mcp]'"

app = typer.Typer(
    invoke_without_command=True,
    help="Serve the run history to coding agents over MCP (stdio); --allow-writes for triage.",
)


@app.callback()
def mcp(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None,
        "--config",
        help=(
            "qasight.yaml of the project to serve (default: auto-discover from the "
            "working directory). Its folder becomes the working directory, so its database "
            "and run snapshots are found when the agent starts the server elsewhere."
        ),
    ),
    allow_writes: bool = typer.Option(
        False,
        "--allow-writes",
        help="Also register write tools (mark_failure_cause). Off by default: read-only.",
    ),
) -> None:
    """Serve the run history to a coding agent over MCP (stdio): failures, flaky tests, history.

    `qasight mcp install` adds this server to your agent's MCP config.
    """
    if ctx.invoked_subcommand is not None:
        return
    try:
        import mcp as _sdk  # noqa: F401
    except ModuleNotFoundError:
        typer.echo(MISSING_EXTRA, err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT)) from None

    if config is not None:
        config = config.expanduser().resolve()
        if not config.is_file():
            typer.echo(f"config file not found: {config}", err=True)
            raise typer.Exit(int(EngineExitCode.INVALID_INPUT))
        # Before anything resolves the database URL or the data root from the cwd.
        os.chdir(config.parent)

    from qasight.config.errors import ConfigDiscoveryError, ConfigError
    from qasight.config.loader import discover_and_load
    from qasight.mcp_server import HistoryTools, build_server, quarantine_rules

    try:
        cfg = discover_and_load(config_path=config)
    except ConfigDiscoveryError:
        if config is not None:
            raise
        cfg = None  # ingested cycles need no config
    except ConfigError as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT)) from exc

    if config is not None and not (os.environ.get("DATABASE_URL") or "").strip():
        # A config with a non-default file name isn't found by DB discovery; read it here.
        from qasight.config.database_section import (
            extract_database_url_from_mapping,
            load_raw_config_dict,
        )

        url = extract_database_url_from_mapping(load_raw_config_dict(config))
        if url:
            os.environ["DATABASE_URL"] = url

    server = build_server(HistoryTools(quarantine_rules(cfg)), allow_writes=allow_writes)
    # stdout is the protocol channel: nothing else may print there.
    server.run("stdio")


@app.command("install")
def install_cmd(
    client: str = typer.Option(
        "claude-code",
        "--client",
        help="claude-code (.mcp.json), cursor (.cursor/mcp.json), vscode (.vscode/mcp.json) "
        "or claude-desktop (its per-machine config file).",
    ),
    directory: Path = typer.Option(Path("."), "--dir", help="The project folder."),
    allow_writes: bool = typer.Option(
        False, "--allow-writes", help="Let the agent mark failure causes too."
    ),
) -> None:
    """Add `qasight mcp` to a coding agent's MCP config for this project."""
    from qasight.services.mcp_install import CLIENTS, install

    name = client.strip().lower()
    if name not in CLIENTS:
        raise typer.BadParameter(f"pick one of {', '.join(CLIENTS)}", param_hint="--client")
    project = directory.expanduser().resolve()
    config = next(
        (project / n for n in CONFIG_FILE_NAMES if (project / n).is_file()),
        None,
    )
    try:
        result = install(name, project, config=config, allow_writes=allow_writes)
    except (OSError, ValueError) as exc:
        typer.echo(f"Not written: {exc}", err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT)) from None
    typer.echo(f"{'Updated' if result.replaced else 'Added'} qasight in {result.path}.")
    try:
        import mcp as _sdk  # noqa: F401
    except ModuleNotFoundError:
        typer.echo(MISSING_EXTRA)
    if config is None:
        typer.echo(
            "No qasight.yaml here: the server reads the history from the folder the agent "
            "starts it in (or DATABASE_URL)."
        )
    restart = "Restart Claude Desktop" if name == "claude-desktop" else "Restart the agent"
    typer.echo(f"{restart}, then ask it: what's failing, and is it flaky?")
