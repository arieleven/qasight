"""Run one cycle end to end: trigger gate, engine, reporters, report archive.

This is the application-layer use case behind ``testo run``,
``POST /api/v1/cycles/{cycle}/executions``, ``POST /api/v1/adhoc-executions``
(a one-stage cycle built by :func:`single_stage_plan`) and ``testo ingest``
(a one-stage ``junit`` cycle built by :func:`junit_ingest_plan`). All of them call
:meth:`CycleRunService.run` and only differ in how they present progress:
the engine streams events to the *renderer* they pass in, and the few
non-engine moments (trigger verdict, archive result) go to an optional
:class:`CycleRunListener`.

Quarantine rules are settled here too (:func:`quarantine_for_run`): the
config's and the list stored in the history database, so every one of those
entry points mutes the same tests.

Nothing here prints on its own except the configured reporters, which take a
Rich console for their progress lines.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import shutil
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from testo_core.services.test_selection import Selection

from testo_core import plugins
from testo_core.config.errors import ConfigError, ConfigValidationError
from testo_core.config.resolver import resolve_stages_for_plan
from testo_core.config.schema import (
    DEFAULT_TIER_BY_FRAMEWORK,
    Plan,
    Stage,
    TestosteroneConfig,
)
from testo_core.config.triggers import (
    TriggerResult,
    evaluate_cycle_trigger,
    persist_trigger_snapshot,
)

logger = logging.getLogger(__name__)


class NoStagesEnabledError(ConfigError):
    """The cycle resolved to zero stages in this environment (every ``if:`` was false)."""


@dataclass(frozen=True)
class CycleRunOptions:
    """Knobs shared by the CLI flags and the API request body."""

    persist: bool = True
    force: bool = False
    fail_fast: bool = False
    workers_override: int | None = None
    reporter_override: Sequence[str] | None = None
    report_db: bool = True
    async_report_db: bool = False
    artifacts_root: Path | None = None
    parent_env: Mapping[str, str] | None = None
    ci: bool = False
    # Extra keys for the run record, e.g. CI provenance sent along with an
    # upload (the server's own environment says nothing about the client's CI).
    run_metadata: Mapping[str, str | None] | None = None
    # Coverage reports the stages write (``--coverage``), read when they finish.
    coverage_reports: Sequence[Path] = ()
    # Run only these tests (``--test``, ``--rerun-failed``; services/test_selection.py).
    selection: Selection | None = None


@dataclass(frozen=True)
class CycleRunOutcome:
    """What happened. ``skipped`` means the trigger found nothing to do (exit 0)."""

    exit_code: int
    plan: Plan
    skipped: bool = False
    run_id: str | None = None
    trigger: TriggerResult | None = None


class CycleRunListener(Protocol):
    """Presentation hooks for the moments the engine's event stream doesn't cover."""

    def trigger_evaluated(self, plan: Plan, result: TriggerResult) -> None: ...

    def trigger_bypassed(self, plan: Plan) -> None: ...

    def report_archived(self, report_id: UUID | None, *, background: bool) -> None: ...


class _SilentListener:
    def trigger_evaluated(self, plan: Plan, result: TriggerResult) -> None:
        return

    def trigger_bypassed(self, plan: Plan) -> None:
        return

    def report_archived(self, report_id: UUID | None, *, background: bool) -> None:
        return


class CycleRunService:
    def __init__(
        self, *, listener: CycleRunListener | None = None, console: Any | None = None
    ) -> None:
        self._listener: CycleRunListener = listener or _SilentListener()
        self._console = console

    def run(
        self,
        *,
        cfg: TestosteroneConfig,
        plan: Plan,
        renderer: Any,
        options: CycleRunOptions | None = None,
    ) -> CycleRunOutcome:
        """Execute *plan* and its post-run steps; return the contract exit code.

        Raises :class:`NoStagesEnabledError` when no stage is enabled.
        """
        opts = options or CycleRunOptions()
        stages = resolve_stages_for_plan(plan)
        if not stages:
            raise NoStagesEnabledError(
                f"plan {plan.name!r} has no stages enabled in this environment."
            )

        trigger: TriggerResult | None = None
        if plan.trigger is not None and not opts.force:
            trigger = evaluate_cycle_trigger(plan=plan, cfg=cfg)
            self._listener.trigger_evaluated(plan, trigger)
            if not trigger.stimulus:
                return CycleRunOutcome(exit_code=0, plan=plan, skipped=True, trigger=trigger)
        elif plan.trigger is not None:
            self._listener.trigger_bypassed(plan)

        effective_plan = apply_workers_override(plan, stages, opts.workers_override)
        effective_plan = quarantine_for_run(cfg=cfg, plan=effective_plan, stored=opts.persist)
        artifacts_root = opts.artifacts_root or cfg.defaults.artifacts_root
        run_metadata = opts.run_metadata
        if opts.selection is not None:
            from testo_core.services import test_selection

            # Raises SelectionError when no stage of the cycle has a chosen test.
            effective_plan = test_selection.narrow_plan(
                effective_plan,
                opts.selection,
                scratch=artifacts_root / ".testo-selection" / effective_plan.name,
            )
            # Listeners that show it (the CLI) implement it; the others don't need to.
            announce = getattr(self._listener, "selection_applied", None)
            if announce is not None:
                announce(effective_plan, opts.selection)
            run_metadata = {
                **(run_metadata or {}),
                test_selection.METADATA_KEY: str(len(opts.selection.tests)),
                "selected_from": opts.selection.source,
            }

        from testo_core.engine.cycle_lock import cycle_lock
        from testo_core.engine.orchestrator import run_plan

        # One run of a cycle at a time per artifacts folder: they share its stage folders.
        with cycle_lock(artifacts_root, effective_plan.name, on_wait=self._say_waiting):
            result = run_plan(
                plan=effective_plan,
                renderer=renderer,
                artifacts_root=artifacts_root,
                parent_env=opts.parent_env,
                persist=opts.persist,
                fail_fast=opts.fail_fast,
                run_metadata=run_metadata,
                coverage_reports=opts.coverage_reports,
            )
            exit_code = int(result.exit_code)
            raw_run_id = result.extra.get("run_id")
            run_id = raw_run_id if isinstance(raw_run_id, str) else None

            run_configured_reporters_for_cycle(
                cfg=cfg,
                plan=effective_plan,
                artifacts_root=artifacts_root,
                run_id=run_id,
                console=self._console,
                ci=opts.ci,
                reporter_override=opts.reporter_override,
            )
            snapshot_native_reports(
                plan=effective_plan, artifacts_root=artifacts_root, run_id=run_id
            )
            push_metrics(plan=effective_plan, artifacts_root=artifacts_root, run_id=run_id)
            if opts.persist and opts.report_db:
                self._archive_cycle_report(
                    artifacts_root=artifacts_root,
                    plan_name=effective_plan.name,
                    exit_code=exit_code,
                    background=opts.async_report_db,
                )

        if opts.persist and run_id:
            # Outside the cycle lock: it talks to GitHub. Off unless TESTO_ISSUES=github;
            # best-effort, never changes the exit code.
            from testo_core.services.flaky_issues import sync_after_run

            sync_after_run(cycle=effective_plan.name, run_id=run_id)
            # TESTO_QUARANTINE_RELEASE_AFTER clean runs release a stored entry, and
            # TESTO_QUARANTINE_AUTO adds failing flaky tests; best-effort.
            from testo_core.services.quarantine_store import quarantine_after_run

            quarantine_after_run(cycle=effective_plan.name, run_id=run_id)

        if (
            trigger is not None
            and trigger.persist_snapshot_after_run
            and exit_code == 0
            and cfg.source_path is not None
            and plan.trigger is not None
        ):
            persist_trigger_snapshot(
                cfg=cfg,
                plan_name=plan.name,
                anchor=cfg.source_path.parent.expanduser().resolve(),
                patterns=plan.trigger.paths,
            )
        return CycleRunOutcome(
            exit_code=exit_code, plan=effective_plan, run_id=run_id, trigger=trigger
        )

    def _say_waiting(self, message: str) -> None:
        if self._console is not None:
            self._console.print(f"[yellow]{message}[/yellow]")
        else:
            logger.warning(message)

    def _archive_cycle_report(
        self, *, artifacts_root: Path, plan_name: str, exit_code: int, background: bool
    ) -> None:
        from testo_core.services.report_archive import try_persist_cycle_report

        def _job() -> UUID | None:
            return try_persist_cycle_report(
                artifacts_root=artifacts_root,
                plan_name=plan_name,
                exit_code_override=exit_code,
            )

        if background:
            threading.Thread(target=_job, daemon=True, name="testo-report-archive").start()
            self._listener.report_archived(None, background=True)
            return
        self._listener.report_archived(_job(), background=False)


ADHOC_PLAN_NAME = "adhoc"
INGEST_STAGE_NAME = "junit"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def single_stage_plan(
    *,
    framework: str,
    target_repo: Path,
    args: Sequence[str] = (),
    timeout_s: float | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> Plan:
    """Wrap one framework invocation in a one-stage plan named ``adhoc``.

    Lets a caller run a framework directly (no ``testosterone.yaml`` cycle)
    while still going through the engine, persistence and reporters.
    Raises :class:`ConfigValidationError` for an unknown framework or a
    missing target directory.
    """
    if framework not in plugins.framework_names():
        allowed = ", ".join(sorted(plugins.framework_names()))
        raise ConfigValidationError(f"unknown framework {framework!r}; expected one of: {allowed}")
    repo = Path(target_repo).expanduser().resolve()
    if not repo.is_dir():
        raise ConfigValidationError(f"target_repo is not a directory: {repo}")
    stage = Stage(
        name=framework,
        framework=framework,
        target_repo=repo,
        args=tuple(args),
        extra_env=tuple(sorted((extra_env or {}).items())),
        tier=DEFAULT_TIER_BY_FRAMEWORK.get(framework, "unit"),
    )
    if timeout_s is not None:
        stage = dataclasses.replace(stage, timeout_s=timeout_s)
    return Plan(name=ADHOC_PLAN_NAME, description=f"Ad-hoc {framework} run", stages=(stage,))


def junit_ingest_plan(
    *,
    cycle: str,
    reports: Sequence[str],
    target_repo: Path,
    stage_name: str = INGEST_STAGE_NAME,
    tier: str = "unit",
) -> Plan:
    """Wrap already-written JUnit XML reports in a one-stage ``junit`` cycle (``testo ingest``).

    *reports* are globs relative to *target_repo*. The cycle name is what the
    run is recorded under, so ingesting into the same name every time gives
    Compare, trends and flaky detection their history. Raises
    :class:`ConfigValidationError` for unsafe names or patterns.
    """
    from testo_core.config.schema import SUPPORTED_TIERS

    for kind, value in (("cycle", cycle), ("stage", stage_name)):
        if not _SAFE_NAME.match(value):
            raise ConfigValidationError(
                f"{kind} name {value!r} must be letters, digits, '.', '_' or '-' "
                "(starting with a letter or digit)."
            )
    if tier not in SUPPORTED_TIERS:
        raise ConfigValidationError(
            f"unsupported tier {tier!r}; expected one of: {sorted(SUPPORTED_TIERS)}"
        )
    patterns = tuple(p.strip() for p in reports if p and p.strip())
    if not patterns:
        raise ConfigValidationError("no JUnit XML reports given.")
    for pattern in patterns:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ConfigValidationError(
                f"report pattern {pattern!r} must be relative to {target_repo} and stay inside it."
            )
    repo = Path(target_repo).expanduser().resolve()
    if not repo.is_dir():
        raise ConfigValidationError(f"target_repo is not a directory: {repo}")
    stage = Stage(
        name=stage_name,
        framework="junit",
        target_repo=repo,
        tier=tier,
        junit_xml=patterns,
    )
    return Plan(name=cycle, description="JUnit results recorded by testo ingest", stages=(stage,))


def quarantine_for_run(*, cfg: TestosteroneConfig, plan: Plan, stored: bool = True) -> Plan:
    """*plan* with every quarantine rule that applies to its cycle.

    Its own rules, then the config cycle's of the same name (an ingest plan is
    built without them), then, when *stored* (the run uses the history
    database), the list kept there (:mod:`testo_core.services.quarantine_store`).
    Reading that list is best-effort: on a database error the run keeps the rest.
    """
    from testo_core.services.quarantine_store import merge_rules, with_stored_rules

    configured = cfg.cycles.get(plan.name)
    config_rules = configured.quarantine if configured is not None else ()
    if stored:
        return with_stored_rules(plan, config_rules=config_rules)
    merged = merge_rules(plan.quarantine, config_rules)
    return plan if merged == plan.quarantine else dataclasses.replace(plan, quarantine=merged)


def apply_workers_override(
    plan: Plan, stages: Sequence[Stage], workers_override: int | None
) -> Plan:
    """Return *plan* with only the resolved *stages*, each forced to *workers_override* when set."""
    if workers_override is not None:
        stages = [
            dataclasses.replace(
                s, workers=int(workers_override), workers_auto=False, workers_explicit=True
            )
            for s in stages
        ]
    return dataclasses.replace(plan, stages=tuple(stages))


def run_configured_reporters_for_cycle(
    *,
    cfg: TestosteroneConfig,
    plan: Plan,
    artifacts_root: Path,
    run_id: str | None,
    console: Any | None,
    ci: bool,
    reporter_override: Sequence[str] | None = None,
) -> None:
    """Run configured HTML reporters (Allure/Extent/ReportPortal/TestBeats) after a cycle.

    Best-effort: the reporters subsystem may not be present in every build
    (see :mod:`testo_core.reporting.reporters`); skip instead of failing the
    run. When *run_id* is known (persistence succeeded), each reporter writes
    directly under ``STATIC_HISTORY_ROOT/<run_id>/`` (Allure per-framework at
    ``allure_reports/<framework>/``, Extent at ``extent_report/``) so the Run
    Detail page's ``GET /api/v1/runs/{run_id}/reports`` can find it.
    """
    config_reporters = getattr(cfg, "reporters", ()) or ()
    if not config_reporters and not reporter_override:
        return

    try:
        from testo_core.reporting.reporters.orchestrate import run_configured_reporters
    except ImportError:
        return

    run_report_root = None
    if run_id:
        from testo_core import paths

        run_report_root = paths.STATIC_HISTORY_ROOT / run_id

    if console is None:
        from rich.console import Console

        console = Console()

    run_configured_reporters(
        cfg=cfg,
        artifacts_root=artifacts_root,
        plan_name=plan.name,
        reporter_override=reporter_override,
        run_id=run_id,
        console=console,
        ci=ci,
        generate_only=True,
        run_report_root=run_report_root,
    )


def push_metrics(*, plan: Plan, artifacts_root: Path, run_id: str | None) -> None:
    """Push the cycle's test KPIs to InfluxDB / Prometheus when configured (best-effort, logged)."""
    from testo_core.paths import plan_artifacts_dir
    from testo_core.reporting.integrations import push_run_metrics_if_configured

    results_root = plan_artifacts_dir(artifacts_root, plan.name)
    for target, ok, message in push_run_metrics_if_configured(
        results_root=results_root, run_id=run_id
    ):
        logger.log(logging.INFO if ok else logging.WARNING, "metrics push %s: %s", target, message)


def snapshot_native_reports(*, plan: Plan, artifacts_root: Path, run_id: str | None) -> None:
    """Copy each stage's own native report (if its adapter produces one)
    under ``STATIC_HISTORY_ROOT/<run_id>/native_reports/<framework>/``.

    Independent of ``reporters:`` config — this just copies an artifact the
    test framework already wrote (e.g. BehaveX's own HTML report), no Allure
    CLI or reporters subsystem involved. Best-effort: a missing native report
    is skipped, an unreadable or uncopyable one is skipped with a warning.
    """
    if not run_id:
        return

    from testo_core import paths
    from testo_core.frameworks.base import get_adapter
    from testo_core.paths import plan_artifacts_dir

    plan_dir = plan_artifacts_dir(artifacts_root, plan.name)
    for stage in plan.stages:
        stage_dir = plan_dir / stage.name
        try:
            adapter = get_adapter(stage.framework)
            native = adapter.native_report(stage_dir)
        except (ValueError, OSError):
            logger.warning("native report lookup failed for stage %s", stage.name, exc_info=True)
            continue
        if native is None or not native.root_dir.is_dir():
            continue

        dest = paths.STATIC_HISTORY_ROOT / run_id / "native_reports" / stage.framework
        try:
            shutil.copytree(native.root_dir, dest, dirs_exist_ok=True)
            entry = dest / native.entry_relpath
            index = dest / "index.html"
            if entry.is_file() and not index.exists():
                shutil.copy2(entry, index)
        except OSError:
            logger.warning(
                "could not snapshot native report of stage %s", stage.name, exc_info=True
            )
            continue
