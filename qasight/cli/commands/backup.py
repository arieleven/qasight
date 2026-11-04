"""``qasight backup`` and ``qasight restore``: the run history as one file, and back."""

from __future__ import annotations

from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode


def backup(
    out: Path = typer.Option(
        None,
        "--out",
        "-o",
        dir_okay=False,
        help="Where to write the backup (default: ./testo-backup-<date>-<time>.tar.gz).",
    ),
    no_files: bool = typer.Option(
        False,
        "--no-files",
        help="Only the database; leave out the per-run result snapshots (static/history/).",
    ),
) -> None:
    """Save every table and each run's result snapshot to one .tar.gz (any database)."""
    from qasight.cli.ui.console import default_console
    from qasight.history.backup import create_backup, default_backup_name

    console = default_console()
    target = out or Path(default_backup_name())
    try:
        summary = create_backup(target, include_files=not no_files)
    except Exception as exc:  # noqa: BLE001 - one line for the user, exit 3
        console.print(f"[fail]Backup failed: {exc}[/]")
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE)) from None
    runs = summary.rows.get("runrecord", 0)
    console.print(
        f"[ok]Backed up {runs} runs ({sum(summary.rows.values())} rows in "
        f"{len(summary.rows)} tables) and {summary.files} files from {summary.dialect} "
        f"to {summary.path}[/]"
    )
    console.print("[muted]It holds account password hashes too: keep it private.[/]")
    raise typer.Exit(code=0)


def restore(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="A qasight backup .tar.gz."),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Delete what the database holds now before restoring (asks first unless --yes).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before --replace deletes."),
) -> None:
    """Load a backup into the active database (DATABASE_URL) and static/history/."""
    from qasight.cli.ui.console import default_console
    from qasight.history.backup import BackupError, existing_runs, restore_backup

    console = default_console()
    if replace and not yes:
        try:
            runs = existing_runs()
        except Exception:  # noqa: BLE001 - the restore below reports a broken database
            runs = 0
        if runs and not typer.confirm(f"Delete the {runs} runs this database has now?"):
            raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    try:
        summary = restore_backup(path, replace=replace)
    except BackupError as exc:
        console.print(f"[fail]{exc}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from None
    except Exception as exc:  # noqa: BLE001 - one line for the user, exit 3
        console.print(f"[fail]Restore failed: {exc}[/]")
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE)) from None
    console.print(
        f"[ok]Restored {summary.rows.get('runrecord', 0)} runs "
        f"({sum(summary.rows.values())} rows) and {summary.files} files.[/]"
    )
    if summary.skipped_tables:
        console.print(
            f"[warn]Skipped tables this version doesn't have: {summary.skipped_tables}[/]"
        )
    for table, columns in summary.dropped_columns.items():
        console.print(f"[warn]{table}: dropped columns this version doesn't have: {columns}[/]")
    raise typer.Exit(code=0)
