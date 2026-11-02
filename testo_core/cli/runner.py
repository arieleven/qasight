"""Bridge module that wires ``testo run`` and ``testo ingest`` to :class:`~testo_core.services.cycle_run.CycleRunService`.

This is the only CLI-side module that knows the renderer classes.  The
service and the engine only see a :class:`testo_core.cli.ui.renderers.Renderer`
protocol instance and a listener for trigger/archive messages.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from rich.console import Console
from rich.panel import Panel

from testo_core.cli.ui.renderers import (
    BufferedRenderer,
    CIRenderer,
    Renderer,
    StreamRenderer,
)
from testo_core.config.errors import ConfigDiscoveryError, ConfigError, PlanNotFoundError
from testo_core.config.loader import discover_and_load, load_config_or_defaults
from testo_core.config.resolver import resolve_plan
from testo_core.config.schema import Plan, TestosteroneConfig
from testo_core.config.triggers import TriggerResult
from testo_core.engine.exit_codes import EngineExitCode

if TYPE_CHECKING:
    from testo_core.services.test_selection import Selection


def _print_config_warnings(warnings: tuple[str, ...], *, console: Console, ci: bool) -> None:
    """Keys the config has that nothing reads; stderr under --ci so stdout stays NDJSON."""
    for message in warnings:
        if ci:
            print(f"warning: {message}", file=sys.stderr)
        else:
            console.print(f"[yellow]warning:[/] {message}")


def execute_plan_command(
    *,
    console: Console,
    plan_name: str | None,
    config_path: Path | None,
    stream: bool,
    ci: bool,
    persist: bool,
    workers_override: int | None,
    force: bool = False,
    report_db: bool = True,
    async_report_db: bool = False,
    coverage: list[Path] | None = None,
    tests: list[str] | None = None,
    rerun_failed: str | None = None,
) -> int:
    """Load + resolve + execute one plan (or every cycle when ``plan_name == 'all'``)."""
    try:
        cfg = discover_and_load(config_path=config_path)
    except (ConfigError, ConfigDiscoveryError) as exc:
        _emit_config_error(console=console, exc=exc, ci=ci)
        return int(EngineExitCode.INVALID_INPUT)
    _print_config_warnings(cfg.warnings, console=console, ci=ci)
    coverage_reports = tuple(p.expanduser().resolve() for p in coverage or ())

    selection = None
    if tests or rerun_failed:
        from testo_core.services import test_selection

        try:
            if plan_name == "all":
                raise test_selection.SelectionError(
                    "--test and --rerun-failed need one cycle, not --cycle all."
                )
            selection = (
                test_selection.from_run_failures(rerun_failed)
                if rerun_failed
                else test_selection.from_names(tests or [])
            )
        except test_selection.SelectionError as exc:
            _emit_config_error(console=console, exc=exc, ci=ci)
            return int(EngineExitCode.INVALID_INPUT)

    if plan_name == "all":
        if coverage_reports:
            _emit_config_error(
                console=console,
                exc=ConfigError("--coverage needs one cycle, not --cycle all."),
                ci=ci,
            )
            return int(EngineExitCode.INVALID_INPUT)
        if not cfg.cycles:
            _emit_config_error(
                console=console,
                exc=ConfigError("no cycles defined in configuration."),
                ci=ci,
            )
            return int(EngineExitCode.INVALID_INPUT)
        worst = 0
        for name in sorted(cfg.cycles.keys()):
            ec = _execute_one_cycle(
                cfg=cfg,
                plan=cfg.cycles[name],
                console=console,
                stream=stream,
                ci=ci,
                persist=persist,
                workers_override=workers_override,
                force=force,
                report_db=report_db,
                async_report_db=async_report_db,
            )
            worst = max(worst, ec)
            if ec in (EngineExitCode.INTERRUPTED, EngineExitCode.TERMINATED):
                return ec  # Ctrl-C / SIGTERM stops the remaining cycles too
        return worst

    try:
        plan = resolve_plan(cfg, plan_name=plan_name)
    except PlanNotFoundError as exc:
        _emit_config_error(console=console, exc=exc, ci=ci)
        return int(EngineExitCode.INVALID_INPUT)

    return _execute_one_cycle(
        cfg=cfg,
        plan=plan,
        console=console,
        stream=stream,
        ci=ci,
        persist=persist,
        workers_override=workers_override,
        force=force,
        report_db=report_db,
        async_report_db=async_report_db,
        coverage_reports=coverage_reports,
        selection=selection,
    )


def execute_ingest_command(
    *,
    console: Console,
    reports: list[str],
    cycle: str,
    stage: str,
    tier: str,
    repo: Path,
    config_path: Path | None,
    ci: bool,
    persist: bool,
    server: str | None = None,
    token: str | None = None,
    coverage: list[str] | None = None,
) -> int:
    """Record existing test result files (JUnit XML and the other known formats) as a run of *cycle*, through the same service as ``testo run``.

    With *server*, upload the files to that server's ``POST /api/v1/ingest``
    instead; it records them through the same service on its side.
    """
    from testo_core.services.cycle_run import junit_ingest_plan

    repo_dir = repo.expanduser().resolve()
    try:
        cfg = load_config_or_defaults(config_path=config_path)
        patterns = report_patterns(reports, repo=repo_dir)
        plan = junit_ingest_plan(
            cycle=cycle.strip(),
            reports=patterns,
            target_repo=repo_dir,
            stage_name=stage.strip(),
            tier=tier.strip().lower(),
        )
        files = matching_reports(patterns, repo=repo_dir)
        if not files:
            raise ConfigError(f"no report file matches {list(patterns)} in {repo_dir}.")
        coverage_files = _coverage_files(coverage or [], repo=repo_dir)
    except ConfigError as exc:
        _emit_config_error(console=console, exc=exc, ci=ci)
        return int(EngineExitCode.INVALID_INPUT)

    if server:
        from testo_core.reporting.coverage_import import parse_coverage

        extra = parse_coverage(coverage_files).as_metadata() if coverage_files else {}
        return _upload_to_server(
            console=console,
            server=server,
            token=token,
            plan=plan,
            files=files,
            ci=ci,
            extra_metadata=extra,
        )

    return _execute_one_cycle(
        cfg=cfg,
        plan=plan,
        console=console,
        stream=False,
        ci=ci,
        persist=persist,
        workers_override=None,
        force=True,
        report_db=persist,
        coverage_reports=tuple(coverage_files),
    )


def _coverage_files(raw: list[str], *, repo: Path) -> list[Path]:
    """``--coverage`` files (relative to *repo*), checked now so a typo fails before recording."""
    from testo_core.reporting.coverage_import import CoverageFormatError, parse_coverage

    found = matching_reports(report_patterns(raw, repo=repo), repo=repo) if raw else []
    if raw and not found:
        raise ConfigError(f"no coverage report matches {raw} in {repo}.")
    try:
        parse_coverage(found)
    except CoverageFormatError as exc:
        raise ConfigError(f"--coverage: {exc}") from exc
    return found


def _upload_to_server(
    *,
    console: Console,
    server: str,
    token: str | None,
    plan: Plan,
    files: list[Path],
    ci: bool,
    extra_metadata: dict[str, str] | None = None,
) -> int:
    from testo_core.services.ingest_client import (
        IngestUploadError,
        client_run_metadata,
        upload_reports,
    )

    (stage,) = plan.stages
    try:
        body = upload_reports(
            server=server,
            token=token,
            cycle=plan.name,
            files=files,
            stage=stage.name,
            tier=stage.tier,
            metadata={**client_run_metadata(), **(extra_metadata or {})},
        )
    except IngestUploadError as exc:
        if ci:
            from testo_core.cli.ui.ci_renderer import emit_ndjson

            emit_ndjson({"event": "error", "code": "infra_failure", "message": str(exc)})
        else:
            console.print(f"[fail]error:[/] {exc}")
        return int(EngineExitCode.INFRA_FAILURE)

    exit_code = int(body.get("exit_code", EngineExitCode.INTERNAL_ERROR))
    if ci:
        from testo_core.cli.ui.ci_renderer import emit_ndjson

        emit_ndjson({"event": "ingest_uploaded", "server": server, **body})
        return exit_code
    style = "ok" if exit_code == 0 else "fail"
    console.print(
        f"[{style}]{body.get('tests', 0)} test(s), {body.get('failed', 0)} failed,[/] "
        f"recorded on {server} as cycle [bold]{body.get('cycle')}[/] "
        f"(run {body.get('run_id') or 'not saved'})"
    )
    if body.get("run_url"):
        console.print(f"[muted]{body['run_url']}[/]")
    return exit_code


def report_patterns(reports: list[str], *, repo: Path) -> tuple[str, ...]:
    """Turn ``testo ingest`` arguments into globs relative to *repo*.

    The shell usually expands globs into absolute or relative file paths; an
    absolute one must sit inside *repo*. A directory (``allure-results/``)
    stands for the files directly in it.
    """
    out: list[str] = []
    for raw in reports:
        text = raw.strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if path.is_absolute():
            try:
                text = path.resolve().relative_to(repo).as_posix()
            except ValueError:
                raise ConfigError(
                    f"report {raw!r} is outside --repo {repo}; pass --repo to a parent directory."
                ) from None
        if (repo / text).is_dir():
            text = f"{text.rstrip('/')}/*"
        out.append(text)
    return tuple(out)


def matching_reports(patterns: tuple[str, ...], *, repo: Path) -> list[Path]:
    """Files under *repo* that *patterns* match (what the ``junit`` stage will read)."""
    found: list[Path] = []
    from testo_core.reporting.junit_import import report_paths

    for pattern in patterns:
        for match in report_paths(repo, pattern):
            resolved = match.resolve()
            if resolved.is_file() and repo in resolved.parents and resolved not in found:
                found.append(resolved)
    return found


def _execute_one_cycle(
    *,
    cfg: TestosteroneConfig,
    plan: Plan,
    console: Console,
    stream: bool,
    ci: bool,
    persist: bool,
    workers_override: int | None,
    force: bool,
    report_db: bool = True,
    async_report_db: bool = False,
    coverage_reports: tuple[Path, ...] = (),
    selection: Selection | None = None,
) -> int:
    from testo_core.services.cycle_run import CycleRunOptions, CycleRunService, NoStagesEnabledError
    from testo_core.services.test_selection import SelectionError

    service = CycleRunService(
        listener=_ConsoleCycleListener(console=console, ci=ci), console=console
    )
    try:
        outcome = service.run(
            cfg=cfg,
            plan=plan,
            renderer=_pick_renderer(console=console, stream=stream, ci=ci),
            options=CycleRunOptions(
                persist=persist,
                force=force,
                workers_override=workers_override,
                report_db=report_db,
                async_report_db=async_report_db,
                ci=ci,
                coverage_reports=coverage_reports,
                selection=selection,
            ),
        )
    except (NoStagesEnabledError, SelectionError) as exc:
        _emit_config_error(console=console, exc=exc, ci=ci)
        return int(EngineExitCode.INVALID_INPUT)
    return outcome.exit_code


class _ConsoleCycleListener:
    """Renders the service's trigger and archive moments as Rich panels or NDJSON."""

    def __init__(self, *, console: Console, ci: bool) -> None:
        self._console = console
        self._ci = ci

    def trigger_evaluated(self, plan: Plan, result: TriggerResult) -> None:
        _emit_cycle_trigger_event(ci=self._ci, plan=plan, tr=result)
        if result.stimulus:
            _emit_cycle_activating(console=self._console, ci=self._ci, plan=plan, tr=result)
        else:
            _emit_cycle_resting(console=self._console, ci=self._ci, plan=plan)

    def selection_applied(self, plan: Plan, selection: Selection) -> None:
        from testo_core.services.test_selection import describe

        if self._ci:
            from testo_core.cli.ui.ci_renderer import emit_ndjson

            emit_ndjson(
                {
                    "event": "selection",
                    "source": selection.source,
                    "tests": len(selection.tests),
                    "stages": [s.name for s in plan.stages],
                }
            )
            return
        self._console.print(
            f"[muted]Running {len(selection.tests)} chosen test(s) ({selection.source}):[/]"
        )
        for line in describe(plan):
            self._console.print(f"[muted]  {line}[/]")

    def trigger_bypassed(self, plan: Plan) -> None:
        if not self._ci:
            self._console.print("[muted]Trigger bypassed (--force).[/]")

    def report_archived(self, report_id: UUID | None, *, background: bool) -> None:
        if self._ci:
            return
        if background:
            self._console.print(
                "[dim]Report database archive started in background "
                "(may not complete if the process exits immediately).[/]"
            )
        elif report_id is not None:
            self._console.print(
                f"[muted]Recorded run[/] [bold]{report_id}[/] [muted](see it with `testo ui`)[/]"
            )


def _emit_cycle_trigger_event(*, ci: bool, plan: Plan, tr: TriggerResult) -> None:
    if not ci:
        return
    from testo_core.cli.ui.ci_renderer import emit_ndjson

    emit_ndjson(
        {
            "event": "cycle_trigger",
            "cycle": plan.name,
            "status": "activated" if tr.stimulus else "resting",
            "reason": tr.reason,
            "matched": list(tr.matched_paths),
            "mode": tr.mode,
        }
    )


def _emit_cycle_resting(*, console: Console, ci: bool, plan: Plan) -> None:
    if ci:
        return
    msg = f"Cycle {plan.name} skipped: No stimulus detected in targeted muscle groups."
    console.print(Panel(msg, title="Resting", border_style="dim"))


def _emit_cycle_activating(*, console: Console, ci: bool, plan: Plan, tr: TriggerResult) -> None:
    if ci:
        return
    hint = tr.matched_paths[0] if tr.matched_paths else ""
    if not hint and plan.trigger is not None and plan.trigger.paths:
        hint = plan.trigger.paths[0]
    body = f"[ok]Stimulus detected[/] in [bold]{hint}[/]. [bold]Activating Cycle:[/] {plan.name}."
    console.print(Panel(body, title="Trigger", border_style="green"))


def _pick_renderer(*, console: Console, stream: bool, ci: bool) -> Renderer:
    if ci:
        return CIRenderer()
    if stream:
        return StreamRenderer(console)
    return BufferedRenderer(console)


def _emit_config_error(*, console: Console, exc: Exception, ci: bool) -> None:
    if ci:
        from testo_core.cli.ui.ci_renderer import emit_ndjson

        emit_ndjson({"event": "error", "code": "invalid_input", "message": str(exc)})
    else:
        console.print(f"[fail]error:[/] {exc}")
