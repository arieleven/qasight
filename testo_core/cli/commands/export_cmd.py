"""``testo export`` — the manager report as a file: PDF, PNG, HTML, PowerPoint, Excel, CSV or JSON."""

from __future__ import annotations

from pathlib import Path

import typer

from testo_core.engine.exit_codes import EngineExitCode


def export(
    fmt: str = typer.Argument(..., help="pdf, png, html, pptx, xlsx, csv or json."),
    out: Path | None = typer.Option(
        None,
        "--out",
        "-o",
        help="File to write (default: e.g. weekly-test-report-2026-11-01.pdf here; '-' for stdout).",
    ),
    period: str = typer.Option(
        "week", "--period", help="day, week, month (30 days) or quarter (90 days)."
    ),
    cycle: list[str] = typer.Option(
        [], "--cycle", help="Only this cycle; repeat for several (default: every cycle)."
    ),
    title: str | None = typer.Option(
        None, "--title", help="Report title (default: 'Weekly test report' and so on)."
    ),
    brand: str = typer.Option(
        "Testosterone", "--brand", help="Name above the title, e.g. your team or product."
    ),
) -> None:
    """Write the manager report (same as the UI's Report page) as a file to share or attach."""
    from testo_core.services.manager_report import PERIOD_DAYS, build_report
    from testo_core.services.report_export import FORMAT_NAMES, ExportUnavailable, file_name
    from testo_core.services.report_export import export as render

    fmt = fmt.strip().lower()
    if fmt not in FORMAT_NAMES or period not in PERIOD_DAYS:
        typer.echo(
            f"format takes {' / '.join(FORMAT_NAMES)} and --period takes {' / '.join(PERIOD_DAYS)}",
            err=True,
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    report = build_report(
        period=period, cycles=[c.strip() for c in cycle if c.strip()], title=title
    )
    try:
        data = render(report, fmt, brand=brand)
    except ExportUnavailable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from None
    if out is not None and str(out) == "-":
        import sys

        sys.stdout.buffer.write(data)
        return
    target = out or Path(file_name(report, fmt))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    k = report.kpis
    typer.echo(
        f"{target}: {report.title}, {k.runs} runs across {k.cycles} cycles "
        f"({len(data) / 1024:.0f} KB)"
    )
