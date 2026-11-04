"""``qasight doctor`` — quick health checks for local QASight setups."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

import typer

from qasight.engine.exit_codes import EngineExitCode

logger = logging.getLogger(__name__)


def _check_node() -> tuple[str, str]:
    node = shutil.which("node")
    if not node:
        return "[warn]WARN[/]", "Node.js not on PATH (required for Allure Report 3)"
    try:
        proc = subprocess.run(  # noqa: S603
            [node, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        ver = (proc.stdout or proc.stderr or "").strip() or "unknown"
        return "[ok]PASS[/]", f"{node} ({ver})"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "[warn]WARN[/]", f"node found but version check failed: {exc}"


def _check_allure3() -> tuple[str, str]:
    from qasight.reporting.allure_cli import (
        AllureCLINotFoundError,
        find_repo_root,
        resolve_allure_command,
    )

    try:
        cmd = resolve_allure_command(repo_root=find_repo_root())
        return "[ok]PASS[/]", " ".join(cmd.argv)
    except (AllureCLINotFoundError, OSError) as exc:
        return "[warn]WARN[/]", str(exc)


def doctor(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to qasight.yaml (defaults to discovery).",
    ),
) -> None:
    """Validate config load, optional DB reachability, and common CLIs on PATH."""
    from rich.table import Table

    from qasight.cli.ui.console import default_console
    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load

    console = default_console()
    table = Table(title="Doctor", show_lines=False)
    table.add_column("Check", style="title")
    table.add_column("Status", style="bold")
    table.add_column("Detail", overflow="fold")

    hard_fail = False
    db_url: str | None = None
    cfg = None

    try:
        cfg = discover_and_load(config_path=config)
        table.add_row("Config load", "[ok]PASS[/]", f"cycles={len(cfg.cycles)}")
    except ConfigError as exc:
        hard_fail = True
        table.add_row("Config load", "[fail]FAIL[/]", str(exc))

    if cfg is not None:
        used_fw: set[str] = set()
        for plan in cfg.cycles.values():
            for st in plan.stages:
                used_fw.add(str(st.framework))
        exe_map = {
            "pytest": "pytest",
            "behave": "behave",
            "behavex": "behavex",
            "playwright": "npx",
            "jest": "npx",
            "vitest": "npx",
            "cypress": "npx",
        }
        for fw in sorted(used_fw):
            exe = exe_map.get(fw)
            if not exe:
                continue
            if shutil.which(exe):
                table.add_row(f"CLI `{exe}`", "[ok]PASS[/]", "on PATH")
            else:
                hard_fail = True
                table.add_row(f"CLI `{exe}`", "[fail]FAIL[/]", "not found on PATH")

    node_status, node_detail = _check_node()
    table.add_row("Node.js", node_status, node_detail)

    allure_status, allure_detail = _check_allure3()
    table.add_row("Allure Report 3 CLI", allure_status, allure_detail)

    docker_path = shutil.which("docker")
    if docker_path:
        table.add_row("Docker CLI", "[ok]PASS[/]", docker_path)
    else:
        table.add_row(
            "Docker CLI", "[warn]WARN[/]", "`docker` not on PATH (optional for some workflows)"
        )

    legacy_java = shutil.which("allure")
    if legacy_java and allure_status.startswith("[warn]"):
        table.add_row(
            "Legacy Allure 2 CLI",
            "[dim]INFO[/]",
            f"Java `allure` at {legacy_java} — Testo uses Allure Report 3 (Node.js) instead",
        )

    if cfg is not None:
        import os

        from qasight.config.database_section import database_url_from_discovered_config

        anchor = cfg.source_path.parent.expanduser().resolve() if cfg.source_path else Path.cwd()
        db_url = (
            os.environ.get("DATABASE_URL") or ""
        ).strip() or database_url_from_discovered_config(cwd=anchor)
        db_url = db_url or None

    if db_url:
        try:
            from sqlalchemy import create_engine, text
        except ImportError:
            table.add_row("Database", "[dim]SKIP[/]", "install qasight[db] for SQLAlchemy probe")
        else:
            try:
                from qasight.repository.db_config import engine_kwargs

                eng = create_engine(db_url, **engine_kwargs(db_url))
                try:
                    with eng.connect() as conn:
                        conn.execute(text("SELECT 1"))
                finally:
                    eng.dispose()
                table.add_row("Database", "[ok]PASS[/]", "connection probe succeeded")
            except Exception as exc:
                # Broad on purpose: any driver/URL/connection error is reported as a FAIL row.
                logger.debug("database probe failed", exc_info=True)
                hard_fail = True
                table.add_row("Database", "[fail]FAIL[/]", str(exc))
    else:
        table.add_row("Database", "[dim]SKIP[/]", "no database.url / DATABASE_URL")

    _storage_rows(table, with_database=bool(db_url))

    from qasight.services.update_check import check as update_check
    from qasight.services.update_check import enabled as update_check_enabled

    update = update_check()
    if update.available:
        table.add_row(
            "qasight",
            "[warn]WARN[/]",
            f"{update.latest} is out (you have {update.current}): pip install -U qasight",
        )
    else:
        detail = update.current
        if update.latest:
            detail += " (the newest)"
        elif not update_check_enabled():
            detail += " (update check off: CI or TESTO_NO_UPDATE_CHECK)"
        table.add_row("qasight", "[ok]PASS[/]", detail)

    console.print(table)
    if hard_fail:
        console.print("[fail]One or more required checks failed.[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    console.print("[ok]Doctor checks passed (optional warnings may remain).[/]")


def _storage_rows(table: Any, *, with_database: bool) -> None:
    """Free disk where the history lives, and (with a database) how big the history is."""
    from qasight.history.storage import (
        disk_space,
        human_bytes,
        low_disk_pct,
        storage_summary,
    )

    disk = disk_space()
    if disk is not None:
        detail = f"{human_bytes(disk.free_bytes)} free ({disk.free_pct:.0f}%) at {disk.path}"
        if disk.free_pct < low_disk_pct():
            table.add_row("Disk", "[warn]WARN[/]", detail + "; set history.retention or free space")
        else:
            table.add_row("Disk", "[ok]PASS[/]", detail)
    if not with_database:
        return
    try:
        summary = storage_summary()
    except Exception as exc:  # noqa: BLE001 - one row, never a crash
        table.add_row("History size", "[dim]SKIP[/]", str(exc))
        return
    parts = [f"{summary.runs if summary.runs is not None else '?'} runs"]
    parts.append(f"database {human_bytes(summary.database_bytes)}")
    parts.append(f"files {human_bytes(summary.history_files_bytes)}")
    if summary.report_archives:
        parts.append(
            f"{summary.report_archives} report archives "
            f"({human_bytes(summary.report_archive_bytes)})"
        )
    table.add_row("History size", "[ok]INFO[/]", ", ".join(parts))
