"""``qasight export`` — every way to write runs out, in one command.

The manager report as PDF, PNG, HTML, PowerPoint, Excel, CSV or JSON, and also
``site`` (the read-only static site, was ``export-site``), ``badge`` (was
``badge``), ``markdown`` (was ``report markdown``) and ``annotations`` (was
``report annotations``). The format can be left out when ``--out`` says it:
``-o report.pdf``, ``-o badge.svg``, ``-o summary.md``, ``-o site`` (a folder). Options the
delegated command takes (``--base-path``, ``--format json``, ``--run-url``) pass
through. The old command names keep working.
"""

from __future__ import annotations

from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode

# Formats that are another command's output: the argv it is run with.
DELEGATES: dict[str, list[str]] = {
    "site": ["export-site"],
    "badge": ["badge"],
    "markdown": ["report", "markdown"],
    "annotations": ["report", "annotations"],
}
_ALIASES = {"md": "markdown", "svg": "badge", "htm": "html", "ppt": "pptx", "xls": "xlsx"}


def _format_from_out(out: Path) -> str:
    """A folder (or a name with no extension) is the static site; else the extension."""
    ext = out.suffix.lstrip(".").lower()
    if out.is_dir() or not ext:
        return "site"
    return _ALIASES.get(ext, ext)


def export(
    ctx: typer.Context,
    fmt: str | None = typer.Argument(
        None,
        help="pdf, png, html, pptx, xlsx, csv, json (the manager report), or site, badge, "
        "markdown, annotations (default: from --out).",
    ),
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
        "QASight", "--brand", help="Name above the title, e.g. your team or product."
    ),
) -> None:
    """Write the manager report (same as the UI's Report page) as a file to share or attach."""
    from qasight.services.manager_report import PERIOD_DAYS, build_report
    from qasight.services.report_export import FORMAT_NAMES, ExportUnavailable, file_name
    from qasight.services.report_export import export as render

    fmt = (fmt or "").strip().lower()
    fmt = _ALIASES.get(fmt, fmt) if fmt != "svg" else "badge"
    if not fmt and out is not None and str(out) != "-":
        fmt = _format_from_out(out)
    if fmt in DELEGATES:
        _delegate(fmt, out=out, cycle=cycle, extra=list(ctx.args))
        return
    if ctx.args:
        typer.echo(f"unexpected arguments for the report: {' '.join(ctx.args)}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    if not fmt:
        typer.echo(
            f"name a format ({' / '.join([*FORMAT_NAMES, *DELEGATES])}) or an --out file "
            "with one as its extension",
            err=True,
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    if fmt not in FORMAT_NAMES or period not in PERIOD_DAYS:
        typer.echo(
            f"format takes {' / '.join([*FORMAT_NAMES, *DELEGATES])} and --period takes "
            f"{' / '.join(PERIOD_DAYS)}",
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
        f"{target}: {report.title}, {k.runs} run{'' if k.runs == 1 else 's'} across "
        f"{k.cycles} cycle{'' if k.cycles == 1 else 's'} "
        f"({len(data) / 1024:.0f} KB)"
    )


def _delegate(fmt: str, *, out: Path | None, cycle: list[str], extra: list[str]) -> None:
    """Run the command that writes *fmt*, with ``--out``, ``--cycle`` and the extra options."""
    from qasight.cli.app import app

    argv = list(DELEGATES[fmt])
    if out is not None:
        argv += ["--out", str(out)]
    cycles = [c.strip() for c in cycle if c.strip()]
    if cycles and fmt != "site":
        argv += ["--cycle", cycles[0]]
    argv += extra
    try:
        code = app(args=argv, standalone_mode=False)
    except SystemExit as exc:  # pragma: no cover - standalone_mode=False returns instead
        code = exc.code
    if isinstance(code, int) and code:
        raise typer.Exit(code=code)
