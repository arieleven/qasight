"""``qasight report list`` and ``qasight report open``: reports archived in the database.

Registered on ``report_app`` in :mod:`qasight.cli.commands.report`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode

logger = logging.getLogger(__name__)


def report_list_archived(
    limit: int = typer.Option(30, "--limit", "-n", help="Maximum number of rows to show."),
) -> None:
    from rich.table import Table

    from qasight.cli.ui.console import default_console
    from qasight.repository.db import get_report_archive_repository

    console = default_console()
    try:
        rows = get_report_archive_repository().list_recent(limit=limit)
    except Exception as exc:
        # Broad on purpose: any DB/driver error maps to exit 3 with its message.
        logger.debug("listing archived reports failed", exc_info=True)
        console.print(f"[fail]could not list reports:[/] {exc}")
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE)) from exc

    if not rows:
        console.print("[muted]No archived reports found.[/]")
        raise typer.Exit(code=0)

    table = Table(title="Archived reports", title_justify="left")
    table.add_column("id", style="bold", overflow="fold", min_width=36, no_wrap=True)
    table.add_column("cycle", style="title")
    table.add_column("created", style="muted")
    table.add_column("exit", justify="right")
    table.add_column("tests", justify="right")
    table.add_column("pass", justify="right")
    table.add_column("fail", justify="right")
    for r in rows:
        table.add_row(
            str(r.id),
            r.cycle_name,
            str(r.created_at),
            str(r.exit_code),
            "—" if r.total_tests is None else str(r.total_tests),
            "—" if r.passed is None else str(r.passed),
            "—" if r.failed is None else str(r.failed),
        )
    console.print(table)
    raise typer.Exit(code=0)


def report_open_archived(
    report_id: str = typer.Option(
        ...,
        "--id",
        help="Report archive UUID (primary key; use qasight report list id column—not headless RunRecord id).",
    ),
    generate_only: bool = typer.Option(
        False,
        "--generate-only",
        help="Write HTML to --out only; do not start the Allure HTTP dashboard.",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host for the Allure dashboard."),
    port: int = typer.Option(
        8080,
        "--port",
        help="Port for the Allure dashboard; if busy, a free port is used automatically. Use 0 to always pick a free port.",
    ),
    out: Path = typer.Option(
        Path("artifacts/report"),
        "--out",
        "-o",
        help="Where to write the generated HTML report.",
    ),
    fmt: str = typer.Option(
        "html",
        "--format",
        "-f",
        help="Output format: html (default), json, or junit.",
    ),
    summary_out: Path | None = typer.Option(
        None,
        "--summary-out",
        help="When --format=json|junit, output file path.",
    ),
) -> None:
    import tempfile
    import uuid

    from qasight.cli.ui.console import default_console
    from qasight.reporting.entry import dispatch_report
    from qasight.repository.db import get_report_archive_repository
    from qasight.services.report_archive import extract_archive_to_plan_dir

    console = default_console()
    try:
        rid = uuid.UUID(str(report_id).strip())
    except (ValueError, TypeError):
        console.print(f"[fail]invalid report id:[/] {report_id!r}")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from None

    try:
        row = get_report_archive_repository().get(rid)
    except Exception as exc:
        # Broad on purpose: any DB/driver error maps to exit 3 with its message.
        logger.debug("loading archived report %s failed", rid, exc_info=True)
        console.print(f"[fail]could not load report:[/] {exc}")
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE)) from exc

    if row is None:
        console.print(f"[fail]no archived report with id[/] {rid}")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    with tempfile.TemporaryDirectory(prefix="testo-report-") as td:
        root = Path(td)
        extract_archive_to_plan_dir(
            zip_bytes=row.artifact_bytes,
            dest_artifacts_root=root,
            plan_name=row.cycle_name,
        )
        exit_code = dispatch_report(
            console=console,
            artifacts_root=root,
            plan_name=row.cycle_name,
            generate_only=generate_only,
            port=port,
            host=host,
            out_dir=out,
            fmt=fmt,
            summary_out=summary_out,
            inject_history=False,
        )
    raise typer.Exit(code=int(exit_code))


def report_delete_archived(
    report_id: list[str] = typer.Option(
        ..., "--id", help="Archive id from `qasight report list` (repeat for more)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Required to confirm the delete."),
) -> None:
    """Delete archived reports from the database. Old ones also go by ``history.retention``."""
    from qasight.cli.ui.console import default_console
    from qasight.repository.db import get_report_archive_repository

    console = default_console()
    if not yes:
        console.print("[fail]Refusing to delete without --yes (or -y).[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    try:
        deleted = get_report_archive_repository().delete(report_id)
    except Exception as exc:
        # Broad on purpose: any DB/driver error maps to exit 3 with its message.
        logger.debug("deleting archived reports failed", exc_info=True)
        console.print(f"[fail]could not delete reports:[/] {exc}")
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE)) from exc
    if not deleted:
        console.print("[warn]No archived report has that id.[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    missing = len(set(report_id)) - deleted
    note = f" ({missing} id(s) not found)" if missing > 0 else ""
    console.print(f"[ok]Deleted {deleted} archived report(s){note}.[/]")
    raise typer.Exit(code=0)
