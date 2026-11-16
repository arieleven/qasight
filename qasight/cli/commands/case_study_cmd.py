"""``qasight case-study`` — before/after numbers for a beta team's case study.

See :mod:`qasight.services.case_study`.
"""

from __future__ import annotations

import json
from datetime import datetime

import typer

from qasight.engine.exit_codes import EngineExitCode


def case_study(
    cycle: list[str] = typer.Option(
        ..., "--cycle", help="Cycle(s) the team runs; repeat for several."
    ),
    since: str = typer.Option(..., "--since", help="The day the team switched (YYYY-MM-DD)."),
    days: int | None = typer.Option(
        None, "--days", min=1, max=365, help="Length of each period (default: since the switch)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead of Markdown."),
) -> None:
    """Compare a period before the switch with one after it: failures, time to fix, causes."""
    from qasight.services.case_study import build, markdown

    try:
        day = datetime.fromisoformat(since)
    except ValueError as exc:
        typer.echo(f"--since must be a date like 2026-11-01, not {since!r}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc
    study = build(cycle, since=day, days=days)
    typer.echo(
        json.dumps(study.as_dict(), indent=2) if as_json else markdown(study), nl=not as_json
    )
