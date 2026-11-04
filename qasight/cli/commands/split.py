"""``qasight split`` — split a cycle's tests into shards of about equal time.

For CI matrix jobs, or one shard per key of a resource pool. pytest users can
skip this and pass ``--qasight-shard I/N`` instead (``qasight/pytest_plugin.py``).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode

# Lines of `pytest --collect-only -q` that aren't tests.
_NOT_A_TEST = re.compile(r"^(\d+ tests? (collected|selected)|no tests ran|=+|-+)", re.IGNORECASE)


def _read_tests(source: str) -> list[str]:
    if source == "-":
        text = sys.stdin.read()
    else:
        text = Path(source).read_text(encoding="utf-8")
    seen: dict[str, None] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not _NOT_A_TEST.match(line):
            seen.setdefault(line, None)
    return list(seen)


def split(
    cycle: str = typer.Option(..., "--cycle", help="Cycle whose test times to use."),
    shards: int = typer.Option(..., "--shards", min=1, help="How many shards."),
    index: int | None = typer.Option(
        None,
        "--index",
        min=1,
        help="Print only this shard's tests (1 to --shards), one per line.",
    ),
    tests: str | None = typer.Option(
        None,
        "--tests",
        help="File with the tests to split, one per line ('-' for stdin), e.g. the output "
        "of `pytest --collect-only -q`. Default: the tests in the cycle's history.",
    ),
    stage: str | None = typer.Option(None, "--stage", help="Only this stage's tests."),
    server: str | None = typer.Option(
        None,
        "--server",
        help="Read the test times from this QASight server (default: $TESTO_SERVER_URL; "
        "token from $TESTO_API_TOKEN).",
    ),
    window: int = typer.Option(20, "--window", min=1, help="Recent runs to take the times from."),
    as_json: bool = typer.Option(False, "--json", help="Print every shard as JSON."),
    allow_empty: bool = typer.Option(
        False,
        "--allow-empty",
        help="With --index, exit 0 (printing nothing) when the shard has no tests. "
        "Default: exit 1, since `pytest $(qasight split ...)` would run every test.",
    ),
) -> None:
    """Split tests into shards that take about the same time, longest tests first."""
    from rich.markup import escape
    from rich.table import Table

    from qasight.cli.ui.console import default_console
    from qasight.services.test_split import (
        SplitError,
        load_test_times,
        nodeid_names,
        plan_split,
        timings_index,
    )

    console = default_console()
    from qasight.cli.server_url import server_url_problem

    problem = server_url_problem(server or os.environ.get("TESTO_SERVER_URL"))
    if problem:
        console.print(f"[fail]{escape(problem)}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    if index is not None and index > shards:
        console.print(f"[fail]--index {index} is more than --shards {shards}.[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    try:
        times = load_test_times(
            cycle.strip(),
            stage=stage,
            server=server or os.environ.get("TESTO_SERVER_URL") or None,
            token=os.environ.get("TESTO_API_TOKEN") or None,
            window=window,
        )
    except SplitError as exc:
        console.print(f"[fail]{escape(str(exc))}[/]")
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE)) from exc
    try:
        names = _read_tests(tests) if tests else sorted({t.name for t in times if t.name})
    except OSError as exc:
        console.print(f"[fail]could not read --tests: {escape(str(exc))}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc
    if not names:
        console.print(
            f"[warn]No tests to split: cycle {cycle!r} has no recorded tests; pass --tests.[/]"
        )
        raise typer.Exit(code=int(EngineExitCode.DOMAIN_FAILURE))

    plan = plan_split(
        names,
        timings_index(times),
        shards,
        names_for=lambda test: nodeid_names(test) if "::" in test else (test,),
    )
    if index is not None:
        picked = plan.shard(index).tests
        if not picked:
            # An empty $(qasight split ...) makes `pytest $(...)` run every test.
            message = (
                f"nothing to run on shard {index} of {shards} ({len(names)} tests for "
                f"{shards} shards)"
            )
            if allow_empty:
                print(f"warning: {message}", file=sys.stderr)
                return
            console.print(f"[fail]{escape(message)}; pass --allow-empty to exit 0 instead.[/]")
            raise typer.Exit(code=int(EngineExitCode.DOMAIN_FAILURE))
        for test in picked:
            typer.echo(test)
        return
    if as_json:
        payload = {
            "cycle": cycle,
            "total": plan.total,
            "timed": plan.timed,
            "shards": [
                {"index": s.index, "estimate_ms": s.estimate_ms, "tests": list(s.tests)}
                for s in plan.shards
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
        return
    table = Table(title=f"{plan.total} tests of {cycle} in {shards} shards")
    table.add_column("Shard", justify="right")
    table.add_column("Tests", justify="right")
    table.add_column("About", justify="right")
    for s in plan.shards:
        about = f"{s.estimate_ms / 1000:.1f}s" if plan.timed else "-"
        table.add_row(str(s.index), str(len(s.tests)), about)
    console.print(table)
    if plan.timed < plan.total:
        console.print(
            f"Times known for {plan.timed} of {plan.total}; the rest count as a typical test."
        )
    console.print("Print one shard's tests with --index I.")
