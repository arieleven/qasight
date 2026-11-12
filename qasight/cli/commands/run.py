"""``qasight run`` — execute a plan defined in qasight.yaml.

This module exposes :func:`run` as a plain function so the parent Typer app
can register it under ``app.command(name="run", ...)``.  The Typer decorator
metadata (option names, help text) is attached via :func:`typer.Option`
defaults below.

The body defers every heavy import (engine, frameworks, DB) until after
argument validation so ``qasight --help`` stays cheap.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer


def run(
    cycle: str = typer.Option(
        None,
        "--cycle",
        help=(
            "Name of the cycle to execute (from 'cycles:' in qasight.yaml). "
            "Use 'all' to run every cycle in sorted order (each trigger evaluated separately)."
        ),
    ),
    plan: str = typer.Option(
        None,
        "--plan",
        "-p",
        help="Deprecated alias for --cycle.",
        hidden=True,
    ),
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        exists=False,
        dir_okay=False,
        readable=True,
        help="Path to a qasight.yaml file (defaults to discovery).",
    ),
    stream: bool = typer.Option(
        False,
        "--stream",
        help="Tail each stage's stdout live instead of waiting for the post-mortem panel.",
    ),
    ci: bool = typer.Option(
        False,
        "--ci",
        help="Emit NDJSON events on stdout instead of Rich panels (machine-readable).",
    ),
    no_persist: bool = typer.Option(
        False,
        "--no-persist",
        help="Skip all post-run persistence (plan_result.json and history database).",
    ),
    no_report_db: bool = typer.Option(
        False,
        "--no-report-db",
        help="Skip archiving cycle artifacts (Allure/JSON) to the report database after the run.",
    ),
    async_report_db: bool = typer.Option(
        False,
        "--async-report-db",
        help="Archive reports in a background thread (may not finish before the process exits).",
    ),
    workers: int = typer.Option(
        None,
        "--workers",
        "-w",
        min=1,
        help="Override the default worker count for parallel-aware frameworks (e.g. BehaveX).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Run the cycle even when a trigger would skip it (ignore selective paths).",
    ),
    coverage: list[Path] = typer.Option(
        None,
        "--coverage",
        help=(
            "Code coverage report the tests write (repeatable): read when the run ends and "
            "stored with it. Cobertura or JaCoCo XML, LCOV, or a Go cover profile."
        ),
    ),
    test: list[str] = typer.Option(
        None,
        "--test",
        "-t",
        help=(
            "Run only this test (repeatable): its name, full name or pytest node id. "
            "pytest, Playwright and Behave stages are narrowed to it; other stages get "
            "ALLURE_TESTPLAN_PATH and TESTO_SELECTED_TESTS."
        ),
    ),
    rerun_failed: str = typer.Option(
        None,
        "--rerun-failed",
        metavar="RUN_ID",
        help="Run only the tests that failed in this recorded run, each in the stage it failed in.",
    ),
    env: list[str] = typer.Option(
        None,
        "--env",
        help=(
            "What this run tests on, KEY=VALUE (repeatable): --env browser=firefox --env os=linux. "
            "Flaky detection and the test page keep environments apart. Default: $TESTO_ENV."
        ),
    ),
    command: list[str] = typer.Argument(
        None,
        metavar="[-- COMMAND...]",
        help=(
            "Run this test command instead of a cycle and record what it ran, no config "
            "needed: qasight run -- pytest -q, qasight run -- npm test, qasight run -- go test ./... "
            "The result-file flag the runner needs is added for you; the exit code is the command's."
        ),
        show_default=False,
    ),
    release: str | None = typer.Option(
        None,
        "--release",
        help=(
            "The release this run tests (2.3, v2.3.0-rc1); the Releases page groups runs by it. "
            "Default: $TESTO_RELEASE."
        ),
    ),
) -> None:
    """Run a plan end-to-end, or any test command after ``--`` (recorded without a config)."""
    from qasight.engine.exit_codes import EngineExitCode
    from qasight.services.run_environment import InvalidEnvironmentPair, apply_cli_pairs

    try:
        apply_cli_pairs(env or [], os.environ)
    except InvalidEnvironmentPair as exc:
        typer.echo(f"error: --env: {exc}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc
    from qasight.services.run_release import InvalidRelease, apply_cli_option

    try:
        apply_cli_option(release, os.environ)
    except InvalidRelease as exc:
        typer.echo(f"error: --release: {exc}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc
    if command:
        if test or rerun_failed or workers:
            typer.echo(
                "error: --test, --rerun-failed and --workers pick tests in a cycle; "
                "with a command, pass them to the command itself.",
                err=True,
            )
            raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
        from qasight.cli.wrap import run_wrapped

        raise typer.Exit(
            code=run_wrapped(
                command,
                cycle=cycle or plan,
                config=config,
                ci=ci,
                persist=not no_persist,
                coverage=[str(c) for c in coverage or []],
            )
        )
    # Deferred imports so `qasight --help` and `qasight plans list` stay light.
    from qasight.cli.runner import execute_plan_command
    from qasight.cli.ui.console import default_console, make_console

    console = make_console(plain=True) if ci else default_console()
    exit_code = execute_plan_command(
        console=console,
        plan_name=cycle if cycle is not None else plan,
        config_path=config,
        stream=stream,
        ci=ci,
        persist=not no_persist,
        workers_override=workers,
        force=force,
        report_db=not no_report_db,
        async_report_db=async_report_db,
        coverage=coverage or [],
        tests=test or [],
        rerun_failed=rerun_failed,
    )
    raise typer.Exit(code=int(exit_code))
