"""``qasight config-db`` — set ``database.url`` in qasight config (YAML or pyproject)."""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode

logger = logging.getLogger(__name__)


def config_db(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file to update (YAML or pyproject.toml). Defaults to the first discovered file.",
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Full SQLAlchemy database URL (skips host/port/user prompts when set).",
    ),
    host: str | None = typer.Option(None, "--host", help="PostgreSQL host (with --url omitted)."),
    port: int | None = typer.Option(None, "--port", help="PostgreSQL port (default 5432)."),
    username: str | None = typer.Option(None, "--username", help="Database user."),
    password: str | None = typer.Option(None, "--password", help="Database password."),
    database: str | None = typer.Option(
        None,
        "--database",
        "--db",
        help="Database name (PostgreSQL / MySQL).",
    ),
    schema: str | None = typer.Option(
        None,
        "--schema",
        help="PostgreSQL search_path (optional); encoded as a connection option.",
    ),
) -> None:
    """Write ``database.url`` into qasight config.

    Merging uses PyYAML / tomli-w and may strip YAML comments. ``DATABASE_URL`` in the
    environment still overrides file-based settings at runtime.
    """
    from qasight.cli.ui.console import default_console
    from qasight.config.database_section import (
        build_postgresql_url,
        discover_config_path,
        merge_database_url_pyproject,
        merge_database_url_yaml,
    )
    from qasight.config.errors import ConfigDiscoveryError, ConfigValidationError
    from qasight.repository.db import reset_repository_cache
    from qasight.repository.db_config import reset_engine_cache, validate_database_url
    from qasight.repository.factory import select_repository_adapter

    console = default_console()

    target = config.expanduser().resolve() if config is not None else discover_config_path()
    if target is None:
        console.print(
            "[fail]No qasight.yaml / qasight.yml / pyproject.toml with "
            "[tool.qasight] found. Pass --config PATH.[/]"
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    if not target.is_file():
        console.print(f"[fail]Config file not found:[/] {target}")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    resolved_url: str
    if url is not None and str(url).strip():
        resolved_url = str(url).strip()
    else:
        h = host or typer.prompt("Database host", default="localhost")
        p = port if port is not None else int(typer.prompt("Port", default="5432"))
        u = username or typer.prompt("Username")
        pw = password or typer.prompt("Password", hide_input=True)
        dbn = database or typer.prompt("Database name")
        sch = (
            schema
            if schema is not None
            else typer.prompt("PostgreSQL schema (optional)", default="")
        )
        sch = sch.strip() or None
        resolved_url = build_postgresql_url(
            host=str(h).strip(),
            port=int(p),
            username=str(u).strip(),
            password=str(pw),
            database=str(dbn).strip(),
            schema=sch,
        )

    try:
        validate_database_url(resolved_url)
        select_repository_adapter(url=resolved_url)
    except ValueError as exc:
        console.print(f"[fail]{exc}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc

    try:
        from sqlalchemy import create_engine, text

        from qasight.repository.db_config import engine_kwargs

        probe = create_engine(resolved_url, **engine_kwargs(resolved_url))
        try:
            with probe.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            probe.dispose()
    except Exception as exc:
        # Broad on purpose: any driver/URL/connection error maps to exit 3 with its message.
        logger.debug("database connection check failed", exc_info=True)
        console.print(f"[fail]Database connection check failed:[/] {exc}")
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE)) from exc

    try:
        if target.suffix.lower() == ".toml":
            merge_database_url_pyproject(path=target, url=resolved_url)
        else:
            merge_database_url_yaml(path=target, url=resolved_url)
    except (ConfigDiscoveryError, ConfigValidationError) as exc:
        console.print(f"[fail]{exc}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc

    reset_engine_cache()
    reset_repository_cache()
    console.print(f"[ok]Wrote database.url to[/] {target}")
    console.print(
        "[dim]Note: ``DATABASE_URL`` env overrides file settings. PyYAML may reorder keys "
        "and remove comments.[/]"
    )
