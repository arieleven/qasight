"""``qasight import reportportal`` — bring a ReportPortal project's history and decisions over.

See :mod:`qasight.services.rp_import`. The API key comes from ``--token`` or
``TESTO_RP_TOKEN``; a key with read access to the project is enough.
"""

from __future__ import annotations

import os

import typer

from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(
    help="Import history from another tool into the run history.",
    no_args_is_help=True,
)

TOKEN_ENV = "TESTO_RP_TOKEN"


@app.command("reportportal")
def reportportal(
    url: str = typer.Option(..., "--url", help="ReportPortal server, e.g. https://rp.example.com"),
    project: str = typer.Option(..., "--project", help="ReportPortal project name."),
    token: str | None = typer.Option(
        None, "--token", help=f"API key (default: ${TOKEN_ENV}).", show_default=False
    ),
    limit: int = typer.Option(100, "--limit", min=1, max=10000, help="Latest launches to import."),
    launch: str | None = typer.Option(None, "--launch", help="Only launches with this name."),
) -> None:
    """Record past launches as runs (dated when they ran) and their defect types as causes."""
    from qasight.services.rp_import import RpClient, RpImportError, import_project

    key = (token or os.getenv(TOKEN_ENV) or "").strip()
    if not key:
        typer.echo(f"pass --token or set {TOKEN_ENV}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    client = RpClient(url=url, project=project.strip(), token=key)
    try:
        report = import_project(client, limit=limit, name=launch, progress=typer.echo)
    except RpImportError as exc:
        typer.echo(f"import stopped: {exc}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc
    for label, error in report.failed:
        typer.echo(f"failed: {label}: {error}", err=True)
    typer.echo(
        f"imported {len(report.imported)} launch(es), skipped {len(report.skipped)}, "
        f"failed {len(report.failed)}; {report.causes} failure cause(s) carried over"
    )
    if report.failed and not report.imported:
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE))
