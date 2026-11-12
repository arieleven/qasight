"""Typer entry-point for the ``qasight`` CLI.

Commands are imported lazily inside their handlers so ``qasight --help`` does
not pay for the cost of importing the engine, framework adapters, or the
optional database layer.
"""

from __future__ import annotations

import sys

import typer

app = typer.Typer(
    name="qasight",
    help="QASight: see what broke, what's flaky and what got slower on every run.",
    no_args_is_help=True,
    add_completion=True,
    pretty_exceptions_show_locals=False,
)


def _version_callback(show_version: bool) -> None:
    if not show_version:
        return
    from qasight.cli.commands.version import resolve_version

    typer.echo(f"qasight {resolve_version()}")
    raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show the qasight version and exit.",
    ),
) -> None:
    """QASight: see what broke, what's flaky and what got slower on every run."""


def _register_commands() -> None:
    """Wire every subcommand at module load time without importing heavy modules.

    Each handler does its own deferred imports (engine, frameworks, DB), so
    only the lightweight Typer + Rich surface is loaded here.
    """
    from qasight.cli.commands import affected as affected_mod
    from qasight.cli.commands import ai as ai_mod
    from qasight.cli.commands import backup as backup_mod
    from qasight.cli.commands import badge as badge_mod
    from qasight.cli.commands import clean as clean_mod
    from qasight.cli.commands import config as config_cmd
    from qasight.cli.commands import config_db as config_db_cmd
    from qasight.cli.commands import demo as demo_mod
    from qasight.cli.commands import diff_cli as diff_cli_mod
    from qasight.cli.commands import doctor as doctor_mod
    from qasight.cli.commands import export_cmd as export_mod
    from qasight.cli.commands import export_site as export_site_mod
    from qasight.cli.commands import flaky as flaky_mod
    from qasight.cli.commands import ingest as ingest_mod
    from qasight.cli.commands import init_cmd as init_cmd_mod
    from qasight.cli.commands import issues as issues_mod
    from qasight.cli.commands import lease as lease_mod
    from qasight.cli.commands import mcp_cmd as mcp_mod
    from qasight.cli.commands import notify as notify_mod
    from qasight.cli.commands import otel as otel_mod
    from qasight.cli.commands import plans as cycles_cmd
    from qasight.cli.commands import plugins as plugins_mod
    from qasight.cli.commands import pool as pool_mod
    from qasight.cli.commands import quarantine as quarantine_mod
    from qasight.cli.commands import report as report_cmd
    from qasight.cli.commands import run as run_cmd
    from qasight.cli.commands import scorecard as scorecard_mod
    from qasight.cli.commands import server_cmd as server_mod
    from qasight.cli.commands import split as split_mod
    from qasight.cli.commands import try_cmd, ui_cmd
    from qasight.cli.commands import users as users_cmd
    from qasight.cli.commands import version as version_cmd
    from qasight.cli.commands import watch as watch_mod

    app.command(
        name="run",
        help="Execute a cycle defined in qasight.yaml.",
        rich_help_panel="Run and report",
    )(run_cmd.run)
    app.command(
        name="ingest",
        help="Record test results (JUnit XML, Jest/Vitest, .trx, go test -json, Robot, Allure, ...) as a run of a cycle.",
        rich_help_panel="Run and report",
    )(ingest_mod.ingest)
    app.command(
        name="ui",
        help="Open the web UI on this machine's run history (http://127.0.0.1:8000).",
        rich_help_panel="Run and report",
    )(ui_cmd.ui)
    app.command(
        name="export-site",
        help="Write the UI and recent runs as a read-only static site (GitHub Pages, any file host).",
        rich_help_panel="Run and report",
    )(export_site_mod.export_site)
    app.add_typer(
        mcp_mod.app,
        name="mcp",
        help="Serve the run history to coding agents over MCP (stdio); `qasight mcp install` sets "
        "up Claude Code, Cursor, VS Code or Claude Desktop.",
        rich_help_panel="Run and report",
    )
    app.command(
        name="demo",
        help="Record eight sample runs (a flaky test, a new failure, a slower test) and open the UI.",
        rich_help_panel="Run and report",
    )(demo_mod.demo)
    app.command(
        name="try",
        help="Preview the pull request comment your latest results would get; records nothing.",
        rich_help_panel="Run and report",
    )(try_cmd.try_comment)
    app.command(
        name="split",
        help="Split a cycle's tests into shards of about equal time (CI matrix, pool keys).",
        rich_help_panel="Run and report",
    )(split_mod.split)
    app.command(
        name="affected",
        help="Pick the tests a change can affect, with why; anything unmapped runs all.",
        rich_help_panel="Run and report",
    )(affected_mod.affected)
    app.command(
        name="notify",
        help="Send a recorded run to Slack or Teams (webhook URLs from the environment).",
        rich_help_panel="Run and report",
    )(notify_mod.notify)
    app.add_typer(
        issues_mod.app,
        name="issues",
        help="Open a GitHub issue per flaky test and close it once the test is stable.",
        rich_help_panel="Run and report",
    )
    app.command(
        name="otel",
        help="Send a recorded run to an OpenTelemetry collector as a trace (OTLP).",
        rich_help_panel="Run and report",
    )(otel_mod.otel)
    app.command(
        name="export",
        help="Write the manager report as a PDF, PNG, HTML, PowerPoint, Excel, CSV or JSON file.",
        rich_help_panel="Run and report",
    )(export_mod.export)
    app.add_typer(
        scorecard_mod.app,
        name="scorecard",
        help="Score a release with the repo review tools (security, dependencies, API, web, load, "
        "tests) on any folder or git URL, compare it with the last version and gate it.",
        rich_help_panel="Run and report",
    )
    app.command(
        name="badge",
        help="Write a README badge for a cycle's latest run (SVG, or shields.io JSON).",
        rich_help_panel="Run and report",
    )(badge_mod.badge)
    app.command(
        name="config-db",
        help="Set database.url in qasight.yaml (or \\[tool.qasight] in pyproject.toml).",
        rich_help_panel="Config",
    )(config_db_cmd.config_db)
    app.command(
        name="diff",
        help="Compare two archived report runs (UUIDs from qasight report list).",
        rich_help_panel="Diagnostics",
    )(diff_cli_mod.diff_reports)
    app.command(
        name="summary",
        help="Rich diff of the two most recent archived runs (optional --cycle).",
        rich_help_panel="Diagnostics",
    )(diff_cli_mod.summary_reports)
    app.command(
        name="flaky",
        help="Tests whose outcome keeps flipping across a cycle's recent runs (--cycle).",
        rich_help_panel="Diagnostics",
    )(flaky_mod.flaky)
    app.add_typer(
        quarantine_mod.app,
        name="quarantine",
        help="Quarantine tests without a config change (list, add, remove), here or on a server.",
        rich_help_panel="Diagnostics",
    )
    app.add_typer(
        plugins_mod.app,
        name="plugins",
        help="List built-in and installed framework and reporter plugins (--check imports them); "
        "`qasight plugins new NAME` starts one.",
        rich_help_panel="Diagnostics",
    )
    app.add_typer(
        cycles_cmd.app,
        name="cycles",
        help="Inspect cycles defined in the config.",
        rich_help_panel="Diagnostics",
    )
    # Backward-compatible alias for muscle memory.
    app.add_typer(
        cycles_cmd.app,
        name="plans",
        help="Deprecated alias for `qasight cycles`.",
        hidden=True,
        rich_help_panel="Diagnostics",
    )
    app.add_typer(
        lease_mod.app,
        name="lease",
        help="Give each parallel worker its own key from a resource pool (resources: in the config).",
        rich_help_panel="Run and report",
    )
    app.add_typer(
        pool_mod.app,
        name="pool",
        help="Compare resource pools with the keys that really exist (read-only, your own commands).",
        rich_help_panel="Run and report",
    )
    app.add_typer(
        ai_mod.app,
        name="ai",
        help="AI failure summaries: set up a provider, check it, summarize a failed run.",
        rich_help_panel="Config",
    )
    app.add_typer(
        users_cmd.app,
        name="users",
        help="Team server accounts and roles (TESTO_AUTH=accounts).",
        rich_help_panel="Config",
    )
    app.add_typer(
        server_mod.app,
        name="server",
        help="Set up a team server: docker compose, a generated token, HTTPS and a first admin.",
        rich_help_panel="Config",
    )
    app.add_typer(
        config_cmd.app,
        name="config",
        help="Validate or scaffold a qasight.yaml.",
        rich_help_panel="Config",
    )
    app.add_typer(
        report_cmd.report_app,
        name="report",
        help=(
            "Unified Allure reports from the latest cycle, or raw framework-native reports "
            "(e.g. BehaveX HTML) via qasight report native."
        ),
        rich_help_panel="Run and report",
    )
    app.command(
        name="version",
        help="Print qasight version.",
        rich_help_panel="About",
    )(version_cmd.version)
    app.command(
        name="doctor",
        help="Health check: config load, DB probe, CLIs on PATH.",
        rich_help_panel="Maintenance",
    )(doctor_mod.doctor)
    app.command(
        name="clean",
        help="Remove artifacts/temp (--yes, --docker) or expired run history (--history).",
        rich_help_panel="Maintenance",
    )(clean_mod.clean)
    app.command(
        name="backup",
        help="Save the run history (every table, plus each run's results) to one .tar.gz.",
        rich_help_panel="Maintenance",
    )(backup_mod.backup)
    app.command(
        name="restore",
        help="Load a qasight backup into the active database (--replace to overwrite).",
        rich_help_panel="Maintenance",
    )(backup_mod.restore)
    app.command(
        name="watch",
        help="Watch files and re-run a cycle (--cycle required).",
        rich_help_panel="Maintenance",
    )(watch_mod.watch)
    app.command(
        name="init",
        help="Set up a project: detect its test runners, write qasight.yaml and the CI workflow, record a first run.",
        rich_help_panel="Config",
    )(init_cmd_mod.init)


_register_commands()


def _click_exception_types() -> tuple[type[BaseException], ...]:
    """``ClickException`` from Click, and from the copy newer Typer vendors.

    Typer 0.2x ships its own ``typer._click`` instead of depending on Click, so
    a usage error can be either class depending on the installed Typer.
    """
    import importlib

    found: list[type[BaseException]] = []
    for module_name in ("click.exceptions", "typer._click.exceptions"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        exc_type = getattr(module, "ClickException", None)
        if isinstance(exc_type, type) and exc_type not in found:
            found.append(exc_type)
    return tuple(found)


def _show_usage_error(exc: BaseException) -> None:
    """Print a usage error the way Typer does in standalone mode."""
    try:
        from typer import rich_utils

        rich_utils.rich_format_error(exc)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - fall back to Click's plain message
        show = getattr(exc, "show", None)
        if callable(show):
            show()


def main(argv: list[str] | None = None) -> int:
    """Public main entry-point referenced by ``[project.scripts] qasight``."""
    usage_errors = _click_exception_types()
    try:
        result = app(args=argv, standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
    except typer.Abort:
        typer.echo("Aborted.", err=True)
        return 1
    except usage_errors as exc:
        # A mistyped command or option, or bare ``qasight``: print the message
        # (or the help) instead of a traceback, with Click's exit code.
        _show_usage_error(exc)
        return int(getattr(exc, "exit_code", 2))
    except ModuleNotFoundError as exc:
        from qasight.cli.missing_module import missing_module_hint

        hint = missing_module_hint(exc.name)
        if hint is None:
            raise
        typer.echo(f"No module named {exc.name!r}. {hint}", err=True)
        from qasight.engine.exit_codes import EngineExitCode

        return int(EngineExitCode.INVALID_INPUT)

    # In ``standalone_mode=False`` Click captures ``typer.Exit`` and returns
    # its exit code instead of raising. Treat the result as an exit code if
    # it looks like one.
    if isinstance(result, int):
        return int(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
