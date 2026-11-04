"""``qasight affected`` — which tests a change can affect, and why each was picked.

Off by default: nothing runs fewer tests unless this command (or
``pytest --qasight-affected``) is used. The selection is in
``qasight/services/affected_tests.py``; when unsure it says "run everything".
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode


def _read_changed(source: str) -> list[str]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    seen: dict[str, None] = {}
    for line in text.splitlines():
        line = line.strip().replace("\\", "/")
        if line and not line.startswith("#"):
            seen.setdefault(line.removeprefix("./"), None)
    return list(seen)


def affected(
    base: str | None = typer.Option(
        None,
        "--base",
        help="Branch or commit to diff against (default: affected.base, else the pull "
        "request's target branch in CI, else origin/main).",
    ),
    changed: str | None = typer.Option(
        None,
        "--changed",
        help="File with the changed files, one per line, relative to the repo root "
        "('-' for stdin), instead of git diff.",
    ),
    coverage: Path | None = typer.Option(
        None,
        "--coverage",
        dir_okay=False,
        help="coverage.py JSON with test contexts (default: affected.coverage).",
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c", dir_okay=False, help="Path to a qasight.yaml file."
    ),
    as_list: bool = typer.Option(
        False,
        "--list",
        help="Print only the tests, one per line. Prints nothing when everything should run.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the selection and reasons as JSON."),
) -> None:
    """Pick the tests a change can affect; anything not mapped runs every test."""
    from rich.markup import escape

    from qasight.cli.ui.console import default_console
    from qasight.config.errors import ConfigError
    from qasight.config.loader import load_config_or_defaults
    from qasight.services.affected_tests import AffectedError, compute

    console = default_console()
    if as_list and as_json:
        console.print("[fail]Pick one of --list and --json.[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    try:
        cfg = load_config_or_defaults(config_path=config)
        changed_list = _read_changed(changed) if changed else None
        selection, _root = compute(
            Path.cwd(),
            cfg.affected,
            base=base.strip() if base else None,
            changed=changed_list,
            coverage_path=coverage,
            env=dict(os.environ),
        )
    except (ConfigError, AffectedError, OSError) as exc:
        console.print(f"[fail]{escape(str(exc))}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc

    if as_list:
        if not selection.run_all:
            for test in selection.tests:
                typer.echo(test)
        return
    if as_json:
        typer.echo(json.dumps(selection.to_json(), indent=2))
        return

    where = f" against {selection.base}" if selection.base else ""
    console.print(f"{len(selection.changed)} changed file(s){escape(where)}.")
    if selection.run_all:
        console.print("[warn]Run every test:[/]")
        for reason in selection.run_all_reasons:
            console.print(f"  - {escape(reason)}")
        return
    if selection.nothing_to_run:
        console.print("[ok]No test needs to run:[/]")
        for decision in selection.files:
            console.print(f"  - {escape(decision.path)}: {escape(decision.reason)}")
        return
    console.print(f"[ok]Run {len(selection.tests)} test(s):[/]")
    for test, reasons in selection.tests.items():
        console.print(f"  {escape(test)}")
        for reason in reasons:
            console.print(f"    [dim]because {escape(reason)}[/]")
    skipped = [d for d in selection.files if d.outcome in ("ignored", "deleted_test")]
    for decision in skipped:
        console.print(f"  [dim]{escape(decision.path)}: {escape(decision.reason)}[/]")
