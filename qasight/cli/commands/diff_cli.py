"""``qasight summary`` (and its old name ``qasight diff``): what changed between two runs."""

from __future__ import annotations

import tempfile
from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode


def _render_diff(
    *,
    console,
    baseline,
    current,
    metrics_only: bool,
) -> None:
    from qasight.cli.ui.summary_dashboard import render_full_diff
    from qasight.services.report_archive_diff import diff_archives

    if metrics_only:
        render_full_diff(
            console,
            baseline=baseline,
            current=current,
            changes=[],
            metrics_only=True,
        )
        return

    with tempfile.TemporaryDirectory(prefix="testo-diff-") as td:
        changes, _meta = diff_archives(baseline=baseline, current=current, tmp=Path(td))

    render_full_diff(
        console,
        baseline=baseline,
        current=current,
        changes=changes,
        metrics_only=False,
    )


def _archive_for(repo, run_or_archive_id):  # noqa: ANN001, ANN202
    """The archive with this id, else the one written for the run with this id."""
    row = repo.get(run_or_archive_id)
    if row is not None:
        return row
    from qasight.repository.db import get_repository

    try:
        run = get_repository().get_run(run_or_archive_id)
    except Exception:  # noqa: BLE001 - no run history: the id simply isn't found
        return None
    if run is None or not run.cycle or run.end_time is None:
        return None
    return repo.find_for_run(cycle_name=run.cycle, finished_at=run.end_time)


def diff_reports(
    baseline_id: str = typer.Argument(
        ...,
        metavar="BASELINE_ID",
        help="Older run: an id (or its first characters) from `qasight runs`, the UI or MCP.",
    ),
    current_id: str = typer.Argument(
        ...,
        metavar="CURRENT_ID",
        help="Newer run (same kinds of id).",
    ),
    metrics_only: bool = typer.Option(
        False,
        "--metrics-only",
        help="Show only denormalized DB columns (no per-test Allure diff).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the comparison as JSON."),
) -> None:
    """Compare two runs; the same as ``qasight summary CURRENT --against BASELINE``.

    Ids from ``qasight report list`` (report archives) still work too.
    """
    from qasight.cli.run_ref import RunRefError, resolve_run

    try:
        resolve_run(current_id)
        resolve_run(baseline_id)
    except RunRefError:
        _diff_archives(baseline_id, current_id, metrics_only=metrics_only)
        return
    _summary(
        run=current_id,
        against=baseline_id,
        cycle=None,
        as_json=as_json,
        metrics_only=metrics_only,
    )


def _diff_archives(baseline_id: str, current_id: str, *, metrics_only: bool) -> None:
    """Compare two report archives by id (``qasight report list``), as ``diff`` always did."""
    from qasight.cli.ui.console import default_console
    from qasight.repository.db import get_report_archive_repository
    from qasight.services.report_archive_diff import parse_archive_uuid

    console = default_console()
    bid = parse_archive_uuid(baseline_id)
    cid = parse_archive_uuid(current_id)
    if bid is None or cid is None:
        console.print("[fail]Each argument must be a run id (a UUID, or its first characters).[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    repo = get_report_archive_repository()
    base = _archive_for(repo, bid)
    cur = _archive_for(repo, cid)
    missing = [i for i, row in ((baseline_id, base), (current_id, cur)) if row is None]
    if missing:
        console.print(
            "[fail]No recorded run with id " + ", ".join(missing) + " (see `qasight runs`).[/]"
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    _render_diff(console=console, baseline=base, current=cur, metrics_only=metrics_only)
    raise typer.Exit(code=0)


def _summary_from_archives(cycle: str | None, *, metrics_only: bool) -> bool:
    """The two newest report archives (of *cycle*), for histories with archives only."""
    from qasight.cli.ui.console import default_console
    from qasight.repository.db import get_report_archive_repository

    repo = get_report_archive_repository()
    rows = (
        repo.list_recent_for_cycle(cycle_name=cycle, limit=2)
        if cycle
        else repo.list_recent(limit=2)
    )
    if len(rows) < 2:
        return False
    current, baseline = rows[0], rows[1]
    console = default_console()
    console.print(
        f"[muted]Comparing[/] [bold]{baseline.id}[/] (baseline) → [bold]{current.id}[/] (current)"
    )
    _render_diff(console=console, baseline=baseline, current=current, metrics_only=metrics_only)
    return True


def summary_reports(
    run: str | None = typer.Argument(
        None,
        metavar="[RUN]",
        help="The run to look at: an id, its first characters, or latest (default: the "
        "cycle's latest run).",
        show_default=False,
    ),
    against: str | None = typer.Option(
        None,
        "--against",
        help="What to compare with: previous (default), last-green, a branch (main) or a run id.",
    ),
    cycle: str | None = typer.Option(
        None,
        "--cycle",
        help="Cycle whose latest run to look at (default: the only cycle in qasight.yaml, "
        "else the newest recorded run's).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print the comparison as JSON (on by itself for coding agents).",
    ),
    metrics_only: bool = typer.Option(
        False, "--metrics-only", help="Only the run totals, no per-test changes.", hidden=True
    ),
) -> None:
    """What changed in a run: new failures, fixes and slower tests, against the run before."""
    _summary(run=run, against=against, cycle=cycle, as_json=as_json, metrics_only=metrics_only)


def _summary(
    *,
    run: str | None,
    against: str | None,
    cycle: str | None,
    as_json: bool,
    metrics_only: bool,
) -> None:
    import json

    from qasight.cli.output_mode import wants_json
    from qasight.cli.run_ref import RunRefError, resolve_baseline, resolve_run
    from qasight.cli.ui.console import default_console

    machine = wants_json(as_json)
    console = default_console()

    def fail(message: str) -> None:
        if machine:
            typer.echo(json.dumps({"error": message}), err=True)
        else:
            console.print(f"[fail]{message}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    try:
        if run:
            current = resolve_run(run, cycle=(cycle or "").strip() or None)
        else:
            from qasight.cli.cycle_pick import require_cycle

            current = resolve_run("latest", cycle=require_cycle(cycle, quiet=machine))
        baseline = resolve_baseline(current, against)
    except RunRefError as exc:
        if not machine and not run and not against:
            picked = (cycle or "").strip() or None
            if _summary_from_archives(picked, metrics_only=metrics_only):
                raise typer.Exit(code=0) from None
        fail(f"{exc}.")
        return

    if machine:
        from qasight.mcp_server import HistoryTools, ToolInputError

        try:
            data = HistoryTools().compare_runs(baseline.run_id, current.run_id)
        except ToolInputError as exc:
            fail(str(exc))
            return
        typer.echo(json.dumps(data, indent=2, default=str))
        raise typer.Exit(code=0)

    from qasight.repository.db import get_report_archive_repository

    repo = get_report_archive_repository()
    base = _archive_for(repo, baseline.run_id)
    cur = _archive_for(repo, current.run_id)
    console.print(
        f"[muted]Comparing[/] [bold]{baseline.run_id[:8]}[/] (baseline) → "
        f"[bold]{current.run_id[:8]}[/] (current), cycle {current.cycle}"
    )
    if base is not None and cur is not None:
        _render_diff(console=console, baseline=base, current=cur, metrics_only=metrics_only)
        raise typer.Exit(code=0)
    # No report archive (recorded with --no-report-db, or on another machine):
    # the run history alone still says what changed.
    from qasight.mcp_server import HistoryTools

    data = HistoryTools().compare_runs(baseline.run_id, current.run_id)
    for line in data.get("highlights") or []:
        console.print(f"  {line}")
    for change in data.get("changes") or []:
        console.print(
            f"  {change['kind']:<14} {change['name']}  "
            f"{change.get('baseline_status')} → {change.get('current_status')}"
        )
    if not data.get("changes"):
        console.print("[ok]No test changed outcome.[/]")
    raise typer.Exit(code=0)
