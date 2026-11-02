"""``testo clean`` — remove local artifacts/temp and optionally prune Docker.

``testo clean --history`` instead deletes expired run history (``history.retention``
in ``testosterone.yaml``, or ``--keep-days`` / ``--keep-runs``): run rows, per-test
rows and ``static/history/<run_id>/`` together. Artifacts are left alone then.
``testo clean --history --run <id>`` deletes just the runs named, whatever the rules say.
"""

from __future__ import annotations

from pathlib import Path

import typer

from testo_core.engine.exit_codes import EngineExitCode


def clean(
    yes: bool = typer.Option(False, "--yes", "-y", help="Required to confirm destructive deletes."),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to testosterone.yaml for resolving defaults.artifacts_root.",
    ),
    temp_dir: Path = typer.Option(
        Path("temp"),
        "--temp-dir",
        help="Relative directory to remove if present (default ./temp).",
    ),
    docker: bool = typer.Option(
        False,
        "--docker",
        help="Prune stopped Docker containers labeled com.testosterone.project=testo.",
    ),
    history: bool = typer.Option(
        False,
        "--history",
        help="Delete expired run history instead of artifacts: runs older than "
        "history.retention.days or beyond history.retention.runs_per_cycle (testosterone.yaml), "
        "with their per-test rows and static/history/<run_id>/ copies.",
    ),
    keep_days: int | None = typer.Option(
        None, "--keep-days", min=1, help="With --history: override history.retention.days."
    ),
    keep_runs: int | None = typer.Option(
        None,
        "--keep-runs",
        min=1,
        help="With --history: override history.retention.runs_per_cycle.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="With --history: list the runs that would go, delete nothing."
    ),
    run: list[str] = typer.Option(
        [],
        "--run",
        help="With --history: delete this run (repeat for more), instead of the expired ones.",
    ),
) -> None:
    """Delete local artifacts and temp output (or, with --history, expired run history)."""
    if run and not history:
        from testo_core.cli.ui.console import default_console

        default_console().print("[fail]--run needs --history (testo clean --history --run ID).[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    if history and run:
        _delete_chosen_runs(run, yes=yes, dry_run=dry_run)
        return
    if history:
        _clean_history(
            yes=yes, config=config, keep_days=keep_days, keep_runs=keep_runs, dry_run=dry_run
        )
        return
    from testo_core.cli.cleanup import (
        docker_prune_stopped_with_label,
        remove_tree_if_exists,
        unsafe_to_remove,
    )
    from testo_core.cli.ui.console import default_console
    from testo_core.config.errors import ConfigError
    from testo_core.config.loader import discover_and_load

    console = default_console()
    if not yes:
        console.print("[fail]Refusing to delete without --yes (or -y).[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    artifacts_root = Path("artifacts").resolve()
    protected: tuple[Path, ...] = ()
    try:
        cfg = discover_and_load(config_path=config)
        artifacts_root = cfg.defaults.artifacts_root.expanduser().resolve()
        if cfg.source_path is not None:
            protected = (cfg.source_path.parent,)
    except ConfigError as exc:
        console.print(f"[warn]Could not load config ({exc}); using ./artifacts for cleanup.[/]")

    td = temp_dir.expanduser().resolve()
    # Check both before deleting either, so a refusal leaves everything in place.
    for target in (artifacts_root, td):
        reason = unsafe_to_remove(target, protected=protected) if target.is_dir() else None
        if reason:
            console.print(
                f"[fail]Refusing to delete {target}: {reason}. Point defaults.artifacts_root "
                "(or --temp-dir) at a folder used only for test output.[/]"
            )
            raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    removed: list[str] = []
    if remove_tree_if_exists(artifacts_root):
        removed.append(str(artifacts_root))
    if remove_tree_if_exists(td):
        removed.append(str(td))

    if removed:
        for p in removed:
            console.print(f"[ok]Removed directory {p}[/]")
    else:
        console.print("[muted]No matching artifact/temp directories found to remove.[/]")

    if docker:
        code, out = docker_prune_stopped_with_label()
        if code == 0:
            console.print("[ok]Docker prune completed for labeled containers.[/]")
            if out.strip():
                console.print(f"[dim]{out.strip()}[/]")
        elif code == 127:
            console.print("[warn]docker CLI not found; skipping container prune.[/]")
        else:
            console.print(f"[fail]docker prune exited {code}: {out.strip()}[/]")
            raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE))

    console.print("[ok]Clean finished.[/]")
    raise typer.Exit(code=0)


def _clean_history(
    *, yes: bool, config: Path | None, keep_days: int | None, keep_runs: int | None, dry_run: bool
) -> None:
    from testo_core.cli.ui.console import default_console
    from testo_core.config.errors import ConfigError
    from testo_core.config.history_section import HistoryRetention
    from testo_core.config.loader import discover_and_load
    from testo_core.history.retention import prune_history

    console = default_console()
    retention = HistoryRetention()
    try:
        retention = discover_and_load(config_path=config).history.retention
    except ConfigError as exc:
        if keep_days is None and keep_runs is None:
            console.print(f"[fail]Could not load config: {exc}[/]")
            raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from None
    if keep_days is not None or keep_runs is not None:
        retention = HistoryRetention(
            days=keep_days if keep_days is not None else retention.days,
            runs_per_cycle=keep_runs if keep_runs is not None else retention.runs_per_cycle,
        )
    if not retention.enabled:
        console.print(
            "[fail]Nothing to prune by: set history.retention.days or runs_per_cycle in "
            "testosterone.yaml, or pass --keep-days / --keep-runs.[/]"
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    if not yes and not dry_run:
        console.print(
            "[fail]Refusing to delete without --yes (or -y); --dry-run shows what would go.[/]"
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    limits = []
    if retention.days is not None:
        limits.append(f"older than {retention.days} day(s)")
    if retention.runs_per_cycle is not None:
        limits.append(f"beyond the newest {retention.runs_per_cycle} per cycle")
    console.print(f"[muted]Retention: runs {' or '.join(limits)}.[/]")

    report = prune_history(retention, dry_run=dry_run)
    if dry_run:
        if report.run_ids:
            console.print(f"[warn]{report.runs_selected} run(s) would be deleted:[/]")
            for run_id in report.run_ids:
                console.print(f"  {run_id}")
        else:
            console.print("[ok]No run has expired.[/]")
        raise typer.Exit(code=0)
    if not report.run_ids:
        console.print("[ok]No run has expired.[/]")
    else:
        console.print(
            f"[ok]Deleted {report.runs_deleted} run(s), {report.test_rows_deleted} test row(s) "
            f"and {report.snapshots_deleted} report cop{'y' if report.snapshots_deleted == 1 else 'ies'}.[/]"
        )
    for error in report.errors:
        console.print(f"[warn]Could not remove {error}[/]")
    if report.errors:
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE))
    console.print("[ok]Clean finished.[/]")
    raise typer.Exit(code=0)


def _delete_chosen_runs(run_ids: list[str], *, yes: bool, dry_run: bool) -> None:
    from testo_core.cli.ui.console import default_console
    from testo_core.history.retention import delete_runs

    console = default_console()
    if not yes and not dry_run:
        console.print(
            "[fail]Refusing to delete without --yes (or -y); --dry-run shows what would go.[/]"
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    report = delete_runs(run_ids, dry_run=dry_run)
    for run_id in report.not_found:
        console.print(f"[warn]No run {run_id} in the run history.[/]")
    for run_id in report.skipped_running:
        console.print(f"[warn]Run {run_id} is still running; left alone.[/]")
    if dry_run:
        if report.run_ids:
            console.print(f"[warn]{report.runs_selected} run(s) would be deleted:[/]")
            for run_id in report.run_ids:
                console.print(f"  {run_id}")
        else:
            console.print("[ok]Nothing to delete.[/]")
        raise typer.Exit(code=0)
    if report.run_ids:
        console.print(
            f"[ok]Deleted {report.runs_deleted} run(s), {report.test_rows_deleted} test row(s) "
            f"and {report.snapshots_deleted} report cop{'y' if report.snapshots_deleted == 1 else 'ies'}.[/]"
        )
    for error in report.errors:
        console.print(f"[warn]Could not remove {error}[/]")
    if report.errors:
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE))
    if not report.run_ids:
        # Every id was wrong or still running: say so with a non-zero exit.
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    raise typer.Exit(code=0)
