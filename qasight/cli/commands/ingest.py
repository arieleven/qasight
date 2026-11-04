"""``qasight ingest`` — record test results that another step already wrote, as a run of a cycle.

The low-effort way in: keep your CI as it is, add one step after the tests,
and every run lands in QASight's history (Compare, trends, ``qasight flaky``,
reporters, PR comment). Works for any runner that writes JUnit XML: pytest,
JUnit5, PHPUnit, GoogleTest, Maestro, ... and also reads runners' own formats,
recognised by content (see :mod:`qasight.reporting.junit_import`):
Playwright and Jest/Vitest JSON, .NET ``.trx``, ``go test -json``, Robot
Framework ``output.xml`` and Allure results folders.

With no file arguments it finds them itself
(:func:`qasight.services.project_setup.find_result_files`), and the cycle
defaults to the folder's name, so plain ``qasight ingest`` after the tests works.

With ``--server`` the files go to a shared QASight server instead, so
every CI job of a team records into one history.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode


def ingest(
    reports: list[str] = typer.Argument(
        None,
        help=(
            "Result files, folders or glob patterns (quote globs: 'reports/**/*.xml'), "
            "relative to --repo or inside it: JUnit XML, Playwright or Jest/Vitest JSON, "
            ".NET .trx, go test -json output, Robot Framework output.xml, "
            "or an allure-results folder. None given: every result file under --repo "
            "that git doesn't track, found by content."
        ),
    ),
    cycle: str = typer.Option(
        None,
        "--cycle",
        help="Cycle to record the run under (default: the --repo folder's name). "
        "Use the same name every time to build its history.",
    ),
    stage: str = typer.Option("junit", "--stage", help="Stage name the tests are grouped under."),
    tier: str = typer.Option(
        "unit", "--tier", help="Test-pyramid tier of these tests: unit, integration or e2e."
    ),
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        file_okay=False,
        help="Directory the report paths are relative to (default: the current directory).",
    ),
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        dir_okay=False,
        help="qasight.yaml for reporters and the database (optional; discovered if present).",
    ),
    ci: bool = typer.Option(
        False,
        "--ci",
        help="Emit NDJSON events on stdout instead of Rich panels (machine-readable).",
    ),
    no_persist: bool = typer.Option(
        False,
        "--no-persist",
        help="Skip all persistence (only useful to check that the reports parse).",
    ),
    server: str = typer.Option(
        None,
        "--server",
        envvar="TESTO_SERVER_URL",
        help="Upload to this QASight server (team history) instead of recording locally.",
    ),
    token: str = typer.Option(
        None,
        "--token",
        envvar="TESTO_API_TOKEN",
        help="The server's API token (default: $TESTO_API_TOKEN).",
    ),
    coverage: list[str] = typer.Option(
        None,
        "--coverage",
        help=(
            "Code coverage report to store with the run (repeatable, relative to --repo): "
            "Cobertura or JaCoCo XML, LCOV, or a Go cover profile."
        ),
    ),
    env: list[str] = typer.Option(
        None,
        "--env",
        help=(
            "What this run tests on, KEY=VALUE (repeatable): --env browser=firefox --env os=linux. "
            "Flaky detection and the test page keep environments apart. Default: $TESTO_ENV."
        ),
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
    """Record test results from any runner (JUnit XML or a runner's own format) as a run of a cycle."""
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
    from qasight.cli.runner import execute_ingest_command
    from qasight.cli.ui.console import default_console, make_console

    console = make_console(plain=True) if ci else default_console()
    from qasight.cli.server_url import server_url_problem

    problem = server_url_problem(server)
    if problem:
        console.print(f"error: {problem}" if ci else f"[fail]{problem}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    from qasight.services.project_setup import (
        cycle_name_for,
        find_result_files,
        result_patterns,
    )

    repo_dir = repo.expanduser().resolve()
    if not reports:
        found = find_result_files(repo_dir)
        if not found:
            message = (
                f"no result files found under {repo_dir} (JUnit XML, Jest/Vitest/Playwright/"
                "RSpec JSON, .trx, go test -json, Robot output.xml, allure-results; files git "
                "tracks are skipped). Pass the files, or make the test step write one."
            )
            console.print(f"error: {message}" if ci else f"[fail]{message}[/]")
            raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
        reports = result_patterns(found, repo=repo_dir)
        if not ci:
            console.print(f"Found {len(reports)} result file(s): {', '.join(reports)}")
    exit_code = execute_ingest_command(
        console=console,
        reports=reports,
        cycle=(cycle or "").strip() or cycle_name_for(repo_dir),
        stage=stage,
        tier=tier,
        repo=repo,
        config_path=config,
        ci=ci,
        persist=not no_persist,
        server=server,
        token=token,
        coverage=coverage or [],
    )
    raise typer.Exit(code=int(exit_code))
