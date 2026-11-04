"""``qasight flaky`` — tests whose outcome keeps flipping across a cycle's recent runs."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from qasight.engine.exit_codes import EngineExitCode

if TYPE_CHECKING:
    from qasight.config.schema import QuarantineRule

_MARK = {"passed": "[ok]✓[/]", "failed": "[fail]✗[/]", "broken": "[fail]✗[/]"}


def flaky(
    cycle: str = typer.Option(..., "--cycle", help="Cycle whose run history to analyze."),
    window: int = typer.Option(
        20, "--window", min=2, help="How many of the cycle's most recent runs to look at."
    ),
    new: bool = typer.Option(
        False, "--new", help="Only tests that the cycle's latest run made flaky."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the report as JSON."),
    fail_on_flaky: bool = typer.Option(
        False,
        "--fail-on-flaky",
        help="Exit 1 when any flaky test that isn't quarantined is found (for CI gates).",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="qasight.yaml whose 'quarantine:' rules mark tests (default: auto-discover). "
        "Tests in the stored list (qasight quarantine) are marked too.",
    ),
) -> None:
    """List flaky tests: passed on retry, mixed results on one commit, or flips on a branch."""
    from rich.markup import escape
    from rich.table import Table

    from qasight.cli.ui.console import default_console
    from qasight.config.errors import ConfigError
    from qasight.services.flaky_service import find_flaky_tests, newly_flaky_tests

    report = find_flaky_tests(cycle=cycle.strip(), window=window)
    if new:
        report = replace(report, flaky=newly_flaky_tests(cycle=report.cycle, window=window))
    try:
        rules = _quarantine_rules(report.cycle, config)
    except ConfigError as exc:
        default_console().print(f"[fail]config error:[/] {escape(str(exc))}")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc
    if rules:
        from qasight.engine.quarantine import matching_rule

        report = replace(
            report,
            flaky=tuple(
                replace(
                    t,
                    quarantined=matching_rule(rules, name=t.name, full_name=t.full_name)
                    is not None,
                )
                for t in report.flaky
            ),
        )
    open_flaky = [t for t in report.flaky if not t.quarantined]
    exit_code = int(EngineExitCode.DOMAIN_FAILURE) if (fail_on_flaky and open_flaky) else 0

    if as_json:
        typer.echo(json.dumps(asdict(report), indent=2))
        raise typer.Exit(code=exit_code)

    console = default_console()
    if report.runs_analyzed < 2:
        from qasight.cli.cycle_hint import unknown_cycle_hint

        console.print(
            f"[warn]Need at least two recorded runs of cycle {report.cycle!r} with per-test "
            f"results; found {report.runs_analyzed}.[/]"
        )
        hint = unknown_cycle_hint(report.cycle) if report.runs_analyzed == 0 else None
        if hint:
            console.print(f"[warn]{escape(hint)}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    console.print(
        f"[muted]{report.tests_analyzed} tests across the last {report.runs_analyzed} runs "
        f"of[/] [bold]{report.cycle}[/]"
    )
    if report.broken and not new:
        broken = Table(
            title=f"Broken, not flaky ({len(report.broken)}): failing every recent run",
            title_justify="left",
        )
        broken.add_column("Test")
        broken.add_column("Group", style="muted")
        broken.add_column("Failing runs", justify="right")
        broken.add_column("Last error", style="muted")
        for b in report.broken:
            broken.add_row(escape(b.name), escape(b.group), str(b.failing_runs), escape(b.message))
        console.print(broken)
    if not report.flaky:
        console.print(f"[ok]No {'newly ' if new else ''}flaky tests.[/]")
        raise typer.Exit(code=exit_code)

    table = Table(title=f"Flaky tests ({len(report.flaky)})", title_justify="left")
    table.add_column("Test")
    table.add_column("Group", style="muted")
    table.add_column("Pass / fail", justify="right")
    table.add_column("Flips", justify="right")
    table.add_column("Why")
    table.add_column("History (oldest → newest)")
    table.add_column("Quarantined")
    for t in report.flaky:
        history = "".join(_MARK.get(s, "[muted]·[/]") for s in t.history)
        table.add_row(
            escape(t.name),
            escape(t.group),
            f"{t.passed} / {t.failed}",
            str(t.flips),
            escape("; ".join(t.reasons)),
            history,
            "[warn]yes[/]" if t.quarantined else "",
        )
    console.print(table)
    if open_flaky:
        console.print(
            "[muted]To stop these failing the build while they're fixed, quarantine them "
            "(no config change; the UI's Quarantine button does the same):[/]"
        )
        for t in open_flaky:
            console.print(
                escape(f"  qasight quarantine add --cycle {report.cycle} {json.dumps(t.name)}"),
                highlight=False,
            )
        console.print(
            f"[muted]Or, with a qasight.yaml, add them to cycle {report.cycle!r} there:[/]"
        )
        snippet = "\n".join(
            ["quarantine:"] + [f"  - test: {json.dumps(t.name)}" for t in open_flaky]
        )
        console.print(escape(snippet), highlight=False)
    raise typer.Exit(code=exit_code)


def _quarantine_rules(cycle: str, config: Path | None) -> tuple[QuarantineRule, ...]:
    """The cycle's config rules (none when no config is found and none was given), then the stored list."""
    from qasight.config.errors import ConfigDiscoveryError
    from qasight.config.loader import discover_and_load
    from qasight.services.quarantine_store import merge_rules, stored_rules

    configured: tuple[QuarantineRule, ...] = ()
    try:
        cfg = discover_and_load(config_path=config)
        plan = cfg.cycles.get(cycle)
        configured = plan.quarantine if plan is not None else ()
    except ConfigDiscoveryError:
        if config is not None:
            raise
    return merge_rules(configured, stored_rules(cycle))
