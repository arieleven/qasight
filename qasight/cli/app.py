"""Typer entry-point for the ``qasight`` CLI.

Commands are imported lazily inside their handlers so ``qasight --help`` does
not pay for the cost of importing the engine, framework adapters, or the
optional database layer.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

import typer

# The one promise ``qasight --help`` leads with (see the Focus Review ADR).
TAGLINE = "Every test run, remembered: what's new, what's flaky and what got slower."

# Panels of the short ``qasight --help``: the ten commands every user needs.
CORE_PANELS = ("Start", "Record", "Understand", "Share")
# Panels the rest of the commands sit in, shown by ``qasight help --all``.
MORE_PANELS = (
    "More: understand",
    "More: release gate",
    "More: AI",
    "More: reports and alerts",
    "More: CI at scale",
    "More: setup and team server",
    "More: maintenance",
)

app = typer.Typer(
    name="qasight",
    help=TAGLINE,
    epilog=(
        "More commands (scorecard, ai, export, affected, split, lease, server, ...): "
        "qasight help --all. For coding agents: qasight help --json."
    ),
    no_args_is_help=True,
    add_completion=True,
    pretty_exceptions_show_locals=False,
)


@dataclass(frozen=True)
class CommandSpec:
    """One top-level command: where ``--help`` lists it, or which command it is an alias of."""

    name: str
    help: str
    panel: str
    core: bool = False
    alias_of: str | None = None


COMMANDS: list[CommandSpec] = []


def _add(target: Any, spec: CommandSpec, *, group: bool = False) -> None:
    """Register ``target`` (a function, or a Typer app when ``group``) under ``spec``."""
    COMMANDS.append(spec)
    hidden = not spec.core
    if group:
        app.add_typer(
            target, name=spec.name, help=spec.help, hidden=hidden, rich_help_panel=spec.panel
        )
    else:
        app.command(name=spec.name, help=spec.help, hidden=hidden, rich_help_panel=spec.panel)(
            target
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
    """Every test run, remembered: what's new, what's flaky and what got slower."""


def _register_commands() -> None:
    """Wire every subcommand at module load time without importing heavy modules.

    Each handler does its own deferred imports (engine, frameworks, DB), so
    only the lightweight Typer + Rich surface is loaded here. Only the core
    commands show in ``qasight --help``; every other command works the same and
    is listed by ``qasight help --all``. Aliases keep old names working.
    """
    from qasight.cli.commands import affected as affected_mod
    from qasight.cli.commands import ai as ai_mod
    from qasight.cli.commands import backup as backup_mod
    from qasight.cli.commands import badge as badge_mod
    from qasight.cli.commands import ci_cmd as ci_mod
    from qasight.cli.commands import clean as clean_mod
    from qasight.cli.commands import config as config_cmd
    from qasight.cli.commands import config_db as config_db_cmd
    from qasight.cli.commands import demo as demo_mod
    from qasight.cli.commands import diff_cli as diff_cli_mod
    from qasight.cli.commands import doctor as doctor_mod
    from qasight.cli.commands import export_cmd as export_mod
    from qasight.cli.commands import export_site as export_site_mod
    from qasight.cli.commands import flaky as flaky_mod
    from qasight.cli.commands import help_cmd as help_mod
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

    # Merged commands: the new home of each old top-level command.
    ci_mod.app.command(
        "preview",
        help="Preview the pull request comment your latest results would get; records nothing.",
    )(try_cmd.try_comment)
    lease_mod.app.add_typer(
        pool_mod.app,
        name="pools",
        help="Compare resource pools with the keys that really exist (read-only, your own commands).",
    )
    config_cmd.app.command(
        "db", help="Set database.url in qasight.yaml (or \\[tool.qasight] in pyproject.toml)."
    )(config_db_cmd.config_db)

    # Core: what ``qasight --help`` shows.
    _add(
        init_cmd_mod.init,
        CommandSpec(
            "init",
            "Set up a project: detect its test runners, write qasight.yaml and the CI workflow, "
            "record a first run.",
            "Start",
            core=True,
        ),
    )
    _add(
        demo_mod.demo,
        CommandSpec(
            "demo",
            "Try it on eight sample runs (a flaky test, a new failure, a slower test) and open the UI.",
            "Start",
            core=True,
        ),
    )
    _add(
        doctor_mod.doctor,
        CommandSpec(
            "doctor",
            "Check the config, the database and the test tools on PATH.",
            "Start",
            core=True,
        ),
    )
    _add(
        run_cmd.run,
        CommandSpec(
            "run",
            "Run a cycle from qasight.yaml, or any test command with no config: qasight run -- pytest.",
            "Record",
            core=True,
        ),
    )
    _add(
        ingest_mod.ingest,
        CommandSpec(
            "ingest",
            "Record results you already have (JUnit XML, Jest/Vitest, Playwright, .trx, go test -json, "
            "Robot, Allure, ...).",
            "Record",
            core=True,
        ),
    )
    _add(
        diff_cli_mod.summary_reports,
        CommandSpec(
            "summary",
            "What changed since the last run: new failures, fixes and slower tests (--cycle).",
            "Understand",
            core=True,
        ),
    )
    _add(
        flaky_mod.flaky,
        CommandSpec(
            "flaky",
            "Tests whose outcome keeps flipping across a cycle's recent runs (--cycle).",
            "Understand",
            core=True,
        ),
    )
    _add(
        ui_cmd.ui,
        CommandSpec(
            "ui",
            "Open the web UI on this machine's run history (http://127.0.0.1:8000).",
            "Understand",
            core=True,
        ),
    )
    _add(
        ci_mod.app,
        CommandSpec(
            "ci",
            "After a CI run: pull/merge request comment, job summary, annotations, chat alerts "
            "(ci preview shows the comment first).",
            "Share",
            core=True,
        ),
        group=True,
    )
    _add(
        mcp_mod.app,
        CommandSpec(
            "mcp",
            "Give coding agents your run history over MCP; `qasight mcp install` sets up Claude Code, "
            "Cursor, VS Code or Claude Desktop.",
            "Share",
            core=True,
        ),
        group=True,
    )

    # More: listed by ``qasight help --all``; each works exactly as before.
    _add(
        diff_cli_mod.diff_reports,
        CommandSpec(
            "diff",
            "Compare two specific archived runs (UUIDs from qasight report list).",
            "More: understand",
        ),
    )
    _add(
        quarantine_mod.app,
        CommandSpec(
            "quarantine",
            "Quarantine tests without a config change (list, add, remove), here or on a server.",
            "More: understand",
        ),
        group=True,
    )
    _add(
        cycles_cmd.app,
        CommandSpec("cycles", "Inspect cycles defined in the config.", "More: understand"),
        group=True,
    )
    _add(
        scorecard_mod.app,
        CommandSpec(
            "scorecard",
            "Score a release with the repo review tools (security, dependencies, API, web, load, "
            "tests) on any folder or git URL, compare it with the last version and gate it.",
            "More: release gate",
        ),
        group=True,
    )
    _add(
        ai_mod.app,
        CommandSpec(
            "ai",
            "AI failure summaries with your own model: set up a provider, check it, summarize a run.",
            "More: AI",
        ),
        group=True,
    )
    _add(
        export_mod.export,
        CommandSpec(
            "export",
            "Write the manager report as a PDF, PNG, HTML, PowerPoint, Excel, CSV or JSON file.",
            "More: reports and alerts",
        ),
    )
    _add(
        export_site_mod.export_site,
        CommandSpec(
            "export-site",
            "Write the UI and recent runs as a read-only static site (GitHub Pages, any file host).",
            "More: reports and alerts",
        ),
    )
    _add(
        badge_mod.badge,
        CommandSpec(
            "badge",
            "Write a README badge for a cycle's latest run (SVG, or shields.io JSON).",
            "More: reports and alerts",
        ),
    )
    _add(
        notify_mod.notify,
        CommandSpec(
            "notify",
            "Send a recorded run to Slack or Teams (webhook URLs from the environment).",
            "More: reports and alerts",
        ),
    )
    _add(
        report_cmd.report_app,
        CommandSpec(
            "report",
            "Allure and framework-native reports (report native), and the run as Markdown "
            "(report markdown).",
            "More: reports and alerts",
        ),
        group=True,
    )
    _add(
        issues_mod.app,
        CommandSpec(
            "issues",
            "Open a GitHub issue per flaky test and close it once the test is stable.",
            "More: reports and alerts",
        ),
        group=True,
    )
    _add(
        otel_mod.otel,
        CommandSpec(
            "otel",
            "Send a recorded run to an OpenTelemetry collector as a trace (OTLP).",
            "More: reports and alerts",
        ),
    )
    _add(
        affected_mod.affected,
        CommandSpec(
            "affected",
            "Pick the tests a change can affect, with why; anything unmapped runs all.",
            "More: CI at scale",
        ),
    )
    _add(
        split_mod.split,
        CommandSpec(
            "split",
            "Split a cycle's tests into shards of about equal time (CI matrix, pool keys).",
            "More: CI at scale",
        ),
    )
    _add(
        lease_mod.app,
        CommandSpec(
            "lease",
            "Give each parallel worker its own key from a resource pool; lease pools checks the "
            "real keys.",
            "More: CI at scale",
        ),
        group=True,
    )
    _add(
        watch_mod.watch,
        CommandSpec(
            "watch", "Watch files and re-run a cycle (--cycle required).", "More: CI at scale"
        ),
    )
    _add(
        config_cmd.app,
        CommandSpec(
            "config",
            "Validate or scaffold a qasight.yaml; config db sets the database.",
            "More: setup and team server",
        ),
        group=True,
    )
    _add(
        plugins_mod.app,
        CommandSpec(
            "plugins",
            "List built-in and installed framework and reporter plugins (--check imports them); "
            "`qasight plugins new NAME` starts one.",
            "More: setup and team server",
        ),
        group=True,
    )
    _add(
        server_mod.app,
        CommandSpec(
            "server",
            "Set up a team server: docker compose, a generated token, HTTPS and a first admin.",
            "More: setup and team server",
        ),
        group=True,
    )
    _add(
        users_cmd.app,
        CommandSpec(
            "users",
            "Team server accounts and roles (TESTO_AUTH=accounts).",
            "More: setup and team server",
        ),
        group=True,
    )
    _add(
        clean_mod.clean,
        CommandSpec(
            "clean",
            "Remove artifacts/temp (--yes, --docker) or expired run history (--history).",
            "More: maintenance",
        ),
    )
    _add(
        backup_mod.backup,
        CommandSpec(
            "backup",
            "Save the run history (every table, plus each run's results) to one .tar.gz.",
            "More: maintenance",
        ),
    )
    _add(
        backup_mod.restore,
        CommandSpec(
            "restore",
            "Load a qasight backup into the active database (--replace to overwrite).",
            "More: maintenance",
        ),
    )

    # Old names that keep working; never listed.
    _add(
        cycles_cmd.app,
        CommandSpec(
            "plans", "Deprecated alias for `qasight cycles`.", "More: understand", alias_of="cycles"
        ),
        group=True,
    )
    _add(
        try_cmd.try_comment,
        CommandSpec("try", "Alias for `qasight ci preview`.", "Share", alias_of="ci preview"),
    )
    _add(
        pool_mod.app,
        CommandSpec(
            "pool", "Alias for `qasight lease pools`.", "More: CI at scale", alias_of="lease pools"
        ),
        group=True,
    )
    _add(
        config_db_cmd.config_db,
        CommandSpec(
            "config-db",
            "Alias for `qasight config db`.",
            "More: setup and team server",
            alias_of="config db",
        ),
    )
    _add(
        version_cmd.version,
        CommandSpec(
            "version",
            "Print the qasight version (same as --version).",
            "More: maintenance",
            alias_of="--version",
        ),
    )

    app.command(
        name="help",
        help="All commands (--all), or every command as JSON for coding agents (--json).",
        hidden=True,
    )(help_mod.help_command)


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
