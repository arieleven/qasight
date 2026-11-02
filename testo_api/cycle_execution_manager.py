from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from testo_api.execution_events import NdjsonCycleListener, append_ndjson_line
from testo_api.run_queue import RunRequest, RunStore, run_store_from_env
from testo_core.config.errors import ConfigDiscoveryError
from testo_core.config.loader import discover_and_load
from testo_core.config.resolver import resolve_plan
from testo_core.config.schema import Defaults, Plan, TestosteroneConfig
from testo_core.engine import interrupt
from testo_core.services.cycle_run import (
    ADHOC_PLAN_NAME,
    CycleRunOptions,
    CycleRunService,
    single_stage_plan,
)

CycleExecutionStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

# What a stopped stage's error and the run's `interrupted_by` say (instead of SIGTERM).
STOP_REASON = "a stop request"

logger = logging.getLogger(__name__)

# The registry only exists so clients can follow a run while it is live; the durable
# record of every run is the run history DB. Running executions are always kept;
# finished ones are kept up to this many (oldest evicted first, minimum 1 so a run's
# final status can always be read), so memory stays bounded however many runs happen.
MAX_FINISHED_EXECUTIONS_ENV = "TESTO_MAX_FINISHED_EXECUTIONS"
DEFAULT_MAX_FINISHED_EXECUTIONS = 200

if TYPE_CHECKING:
    from testo_core.services.test_selection import Selection


# How many executions run at the same time. Each run starts test subprocesses, so the cap
# protects the host; executions over it wait in a FIFO queue with status "queued".
MAX_CONCURRENT_RUNS_ENV = "TESTO_MAX_CONCURRENT_RUNS"
DEFAULT_MAX_CONCURRENT_RUNS = 2


def _positive_int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default


def _max_finished_from_env() -> int:
    return _positive_int_from_env(MAX_FINISHED_EXECUTIONS_ENV, DEFAULT_MAX_FINISHED_EXECUTIONS)


def _max_concurrent_from_env() -> int:
    return _positive_int_from_env(MAX_CONCURRENT_RUNS_ENV, DEFAULT_MAX_CONCURRENT_RUNS)


# Resolves (config, plan) on the worker thread, so config errors surface as a failed execution.
PlanLoader = Callable[[], tuple[TestosteroneConfig, Plan]]


def _adhoc_plan(request: RunRequest) -> Plan:
    return single_stage_plan(
        framework=str(request.framework),
        target_repo=Path(str(request.target_repo)),
        args=request.args,
        timeout_s=request.timeout_s,
        extra_env=request.extra_env,
    )


def _selection(request: RunRequest) -> Selection | None:
    """The chosen tests of a cycle run (``tests`` / ``rerun_failed``), else ``None``."""
    if request.kind != "cycle" or not (request.tests or request.rerun_failed):
        return None
    from testo_core.services import test_selection

    if request.rerun_failed:
        return test_selection.from_run_failures(request.rerun_failed)
    return test_selection.from_names(list(request.tests))


def plan_loader(request: RunRequest) -> PlanLoader:
    """Build the (config, plan) loader for a request; it runs on the worker thread."""
    config_path = Path(request.config_path) if request.config_path is not None else None

    def load_cycle() -> tuple[TestosteroneConfig, Plan]:
        cfg = discover_and_load(config_path=config_path)
        return cfg, resolve_plan(cfg, plan_name=request.cycle)

    def load_adhoc() -> tuple[TestosteroneConfig, Plan]:
        try:
            cfg = discover_and_load(config_path=config_path)
        except ConfigDiscoveryError:
            if config_path is not None:
                raise
            cfg = TestosteroneConfig(version=1, defaults=Defaults())
        return cfg, _adhoc_plan(request)

    return load_adhoc if request.kind == "adhoc" else load_cycle


@dataclass
class CycleExecutionState:
    execution_id: str
    cycle: str
    created_at: float
    status: CycleExecutionStatus = "queued"
    artifacts_root: Path | None = None
    plan_result_path: Path | None = None
    events_path: Path | None = None
    events_start_offset_bytes: int = 0
    # Where this execution's events end in the (per-cycle, shared) events file; set on finish.
    events_end_offset_bytes: int | None = None
    done: bool = False
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    # This run's stop switch: `cancel` pulls it, and only this run's stages stop.
    stop: interrupt.StopScope = field(default_factory=interrupt.StopScope)

    def mark_done(self, *, status: CycleExecutionStatus, error: str | None = None) -> None:
        with self.lock:
            self.status = status
            self.error = error
            self.done = True


class _NullRenderer:
    wants_streaming = False

    def handle(self, _event) -> None:  # noqa: ANN001
        return


class CycleExecutionManager:
    """
    Manage plan/cycle executions using the modern engine lifecycle:

    - `discover_and_load` config discovery + `resolve_plan` (a named cycle), or
      `single_stage_plan` (an ad-hoc run of one framework, see `create_adhoc_execution`)
    - `CycleRunService.run()`, the same use case as `testo run`: trigger gate
      (`cycle_trigger` NDJSON event), `orchestrator.run_plan()` writing durable NDJSON
      into `artifacts/<cycle>/events.ndjson`, reporters and the report archive

    Streaming is done by tailing `events.ndjson` from a recorded byte offset.

    Executions run on a fixed pool of `max_concurrent` worker threads (default from
    `TESTO_MAX_CONCURRENT_RUNS`). A new execution is accepted at once with status
    `queued` and waits in a FIFO queue until a worker is free; one cycle still can't
    be queued or running twice. With `TESTO_RUN_QUEUE=memory` (the default) a restart
    drops queued runs; with `db` they are kept as rows and `recover_queued()` queues
    them again on startup (see `testo_api.run_queue`).

    The registry is bounded: every running execution is kept, plus the last
    `max_finished` finished ones (default from `TESTO_MAX_FINISHED_EXECUTIONS`).
    Older finished executions are evicted and their status/events endpoints answer
    404; their results stay in the run history (`/api/v1/runs`).
    """

    def __init__(
        self,
        *,
        max_finished: int | None = None,
        max_concurrent: int | None = None,
        run_store: RunStore | None = None,
    ) -> None:
        self._states: dict[str, CycleExecutionState] = {}
        self._states_lock = threading.Lock()
        # Queued or running execution per cycle: a cycle runs at most once at a time.
        self._active_by_cycle: dict[str, str] = {}
        configured = _max_finished_from_env() if max_finished is None else max_finished
        self.max_finished = max(1, configured)
        # Finished execution ids, oldest first: the eviction order.
        self._finished: deque[str] = deque()
        concurrent = _max_concurrent_from_env() if max_concurrent is None else max_concurrent
        self.max_concurrent = max(1, concurrent)
        self._queue: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        # Workers start on the first execution, so importing the API starts no threads.
        self._workers: list[threading.Thread] = []
        # Opened on first use (from `TESTO_RUN_QUEUE` unless given), so importing the
        # API opens no database connection.
        self._run_store = run_store
        self._run_store_lock = threading.Lock()

    @property
    def _store(self) -> RunStore:
        with self._run_store_lock:
            if self._run_store is None:
                self._run_store = run_store_from_env()
            return self._run_store

    @property
    def run_queue(self) -> str:
        """``memory`` or ``db``: where queued runs wait (see `testo_api.run_queue`)."""
        return self._store.kind

    def create_execution(
        self,
        *,
        cycle: str,
        config_path: Path | None,
        artifacts_root_override: Path | None = None,
        persist: bool = True,
        force: bool = False,
        fail_fast: bool = False,
        reporter_override: list[str] | None = None,
        report_db: bool = True,
        async_report_db: bool = False,
        workers_override: int | None = None,
        stream: bool = False,
        ci: bool = True,
        tests: Sequence[str] = (),
        rerun_failed: str | None = None,
    ) -> CycleExecutionState:
        request = RunRequest(
            kind="cycle",
            cycle=str(cycle),
            config_path=RunRequest.path_or_none(config_path),
            artifacts_root=RunRequest.path_or_none(artifacts_root_override),
            persist=persist,
            force=force,
            fail_fast=fail_fast,
            reporter_override=tuple(reporter_override) if reporter_override else None,
            report_db=report_db,
            async_report_db=async_report_db,
            workers_override=workers_override,
            stream=stream,
            ci=ci,
            tests=tuple(tests),
            rerun_failed=rerun_failed,
        )
        return self._start(request, plan_loader(request))

    def create_adhoc_execution(
        self,
        *,
        framework: str,
        target_repo: Path,
        args: Sequence[str] = (),
        timeout_s: float | None = None,
        extra_env: Mapping[str, str] | None = None,
        config_path: Path | None = None,
        artifacts_root_override: Path | None = None,
        persist: bool = True,
        report_db: bool = True,
    ) -> CycleExecutionState:
        """Run one framework directly, as a one-stage ``adhoc`` cycle.

        Validates the stage up front (``ConfigValidationError``). Defaults and
        reporters come from the discovered config when there is one.
        """
        request = RunRequest(
            kind="adhoc",
            cycle=ADHOC_PLAN_NAME,
            config_path=RunRequest.path_or_none(config_path),
            artifacts_root=RunRequest.path_or_none(artifacts_root_override),
            persist=persist,
            report_db=report_db,
            stream=True,
            framework=framework,
            target_repo=str(target_repo),
            args=tuple(args),
            timeout_s=timeout_s,
            extra_env=dict(extra_env or {}),
        )
        _adhoc_plan(request)  # validate now, so a bad stage is a 400 and not a failed run
        return self._start(request, plan_loader(request))

    def recover_queued(self) -> int:
        """Queue again the runs an earlier process left waiting (``TESTO_RUN_QUEUE=db``).

        They keep their execution ids, so clients that were following them can go on.
        A run whose cycle is already queued here is dropped. Returns how many were queued.
        """
        recovered = 0
        for pending in self._store.pending():
            try:
                self._start(
                    pending.request,
                    plan_loader(pending.request),
                    execution_id=pending.execution_id,
                    created_at=pending.created_at,
                )
            except RuntimeError:
                logger.warning(
                    "dropping queued run %s: cycle %s is already queued",
                    pending.execution_id,
                    pending.request.cycle,
                )
                self._store.claim(pending.execution_id)
                continue
            recovered += 1
        return recovered

    def _start(
        self,
        request: RunRequest,
        load_plan: PlanLoader,
        *,
        execution_id: str | None = None,
        created_at: float | None = None,
    ) -> CycleExecutionState:
        recovered = execution_id is not None
        execution_id = execution_id or str(uuid4())
        state = CycleExecutionState(
            execution_id=execution_id,
            cycle=request.cycle,
            created_at=time.time() if created_at is None else created_at,
        )

        with self._states_lock:
            existing = self._active_by_cycle.get(request.cycle)
            if existing is not None:
                raise RuntimeError(
                    f"cycle {request.cycle!r} already running (execution_id={existing})"
                )
            self._states[execution_id] = state
            self._active_by_cycle[request.cycle] = execution_id

        if not recovered:
            try:
                self._store.add(execution_id, request, state.created_at)
            except Exception:
                # Broad on purpose: a DB error must not leave the cycle marked busy.
                with self._states_lock:
                    self._states.pop(execution_id, None)
                    self._active_by_cycle.pop(request.cycle, None)
                raise
        self._ensure_workers()
        self._queue.put(lambda: self._claim_and_run(state, request, load_plan))
        return state

    def get(self, execution_id: str) -> CycleExecutionState | None:
        with self._states_lock:
            return self._states.get(execution_id)

    def cancel(self, execution_id: str) -> CycleExecutionState | None:
        """Stop an execution: drop it if it is still queued, stop its stages if it runs.

        A running run's stages get SIGTERM (a second call kills them), the stages not
        started yet are skipped, and the run is still recorded, as stopped. Returns the
        state (``None`` when the id is unknown); a finished run is left as it is.
        """
        state = self.get(execution_id)
        if state is None:
            return None
        with state.lock:
            if state.done:
                return state
            was_queued = state.status == "queued"
            if was_queued:
                # The worker that picks it up sees this and leaves it alone.
                state.status = "cancelled"
        state.stop.request(reason=STOP_REASON)
        if was_queued:
            try:
                self._store.claim(execution_id)  # off the durable queue too
            except Exception:
                # Broad on purpose: the run is stopped either way; a stale row is only logged.
                logger.warning("could not remove queued run %s", execution_id, exc_info=True)
            self._finish(state, status="cancelled", error="Stopped before it started.")
        return state

    def _ensure_workers(self) -> None:
        with self._states_lock:
            # Drop workers that died, so the pool is topped back up to `max_concurrent`.
            self._workers = [w for w in self._workers if w.is_alive()]
            while len(self._workers) < self.max_concurrent:
                # Daemon: a run in progress must not keep the server process alive on exit.
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"testo-run-worker-{len(self._workers) + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                job()
            except BaseException:
                # `_run_execution` already marked the run failed in its `finally`. Catch
                # what it lets through too (SystemExit from code the run imported), so
                # one bad run never shrinks the pool and strands the queue.
                logger.exception("run worker caught an exception that escaped its run")

    def _claim_and_run(
        self, state: CycleExecutionState, request: RunRequest, load_plan: PlanLoader
    ) -> None:
        with state.lock:
            if state.status != "queued":  # stopped while it waited (`cancel`)
                return
            state.status = "running"
        try:
            claimed = self._store.claim(state.execution_id)
        except Exception as exc:
            # Broad on purpose: the run can't start safely, so it fails with the DB's message.
            logger.exception("could not claim queued run %s", state.execution_id)
            self._finish(state, status="failed", error=f"run queue unavailable: {exc}")
            return
        if not claimed:
            # Another server process recovered and started this run; it is theirs now.
            with self._states_lock:
                self._states.pop(state.execution_id, None)
                if self._active_by_cycle.get(state.cycle) == state.execution_id:
                    self._active_by_cycle.pop(state.cycle, None)
            return
        self._run_execution(state, request, load_plan)

    def _run_execution(
        self, state: CycleExecutionState, request: RunRequest, load_plan: PlanLoader
    ) -> None:
        artifacts_root_override = (
            Path(request.artifacts_root) if request.artifacts_root is not None else None
        )
        status: CycleExecutionStatus = "failed"
        error: str | None = "execution interrupted"
        try:
            with interrupt.scoped(state.stop):
                status, error = self._run_in_scope(
                    state, request, load_plan, artifacts_root_override
                )
        except Exception as exc:  # pragma: no cover (defensive: surfaces in API)
            # Broad on purpose: a background run's crash must reach the client as an error event.
            # Keep error exposure minimal (redaction happens upstream in API error formatting where needed).
            logger.exception("execution %s of cycle %s crashed", state.execution_id, state.cycle)
            err = str(exc)
            try:
                if state.events_path is not None:
                    append_ndjson_line(
                        state.events_path,
                        {"event": "error", "code": "internal_error", "message": err},
                    )
            except OSError:
                logger.warning(
                    "could not write error event for %s", state.execution_id, exc_info=True
                )
            error = err
        finally:
            self._finish(state, status=status, error=error)

    def _run_in_scope(
        self,
        state: CycleExecutionState,
        request: RunRequest,
        load_plan: PlanLoader,
        artifacts_root_override: Path | None,
    ) -> tuple[CycleExecutionStatus, str | None]:
        cfg, plan = load_plan()
        artifacts_root = (
            (artifacts_root_override or cfg.defaults.artifacts_root).expanduser().resolve()
        )
        plan_artifacts = (artifacts_root / plan.name).resolve()
        events_path = plan_artifacts / "events.ndjson"
        plan_result_path = plan_artifacts / "plan_result.json"

        plan_artifacts.mkdir(parents=True, exist_ok=True)
        start_offset = 0
        try:
            start_offset = int(events_path.stat().st_size) if events_path.exists() else 0
        except OSError:
            start_offset = 0

        with state.lock:
            state.artifacts_root = artifacts_root
            state.events_path = events_path
            state.plan_result_path = plan_result_path
            state.events_start_offset_bytes = start_offset

        # Stage subprocesses resolve tools (pytest/behave/...) via PATH. The API server
        # may be launched without an activated venv, so prepend this interpreter's bin
        # dir to guarantee stages run against the same environment as the engine.
        parent_env = dict(os.environ)
        # Note: do not resolve() — in a venv, sys.executable is a symlink into the
        # base interpreter; resolving it would point PATH at the wrong bin dir.
        exe_bin = str(Path(sys.executable).parent)
        path_entries = parent_env.get("PATH", "").split(os.pathsep)
        if exe_bin not in path_entries:
            parent_env["PATH"] = os.pathsep.join([exe_bin, *path_entries])

        # Same use case as `testo run`; `run_plan` persists events.ndjson and
        # plan_result.json under artifacts/<cycle>/, which the SSE route tails.
        outcome = CycleRunService(listener=NdjsonCycleListener(events_path, ci=request.ci)).run(
            cfg=cfg,
            plan=plan,
            renderer=_NullRenderer(),
            options=CycleRunOptions(
                persist=request.persist,
                force=request.force,
                fail_fast=request.fail_fast,
                workers_override=request.workers_override,
                reporter_override=list(request.reporter_override)
                if request.reporter_override
                else None,
                report_db=request.report_db,
                async_report_db=request.async_report_db,
                artifacts_root=artifacts_root,
                parent_env=parent_env,
                ci=request.ci,
                selection=_selection(request),
            ),
        )
        if outcome.skipped:
            # Contract: treat resting as success (exit_code 0). Emit a minimal `plan_finished`
            # so UIs relying on a terminal event can close the stream.
            append_ndjson_line(
                events_path,
                {
                    "event": "plan_finished",
                    "plan": plan.name,
                    "aggregate_returncode": 0,
                    "exit_code": 0,
                    "duration_s": 0.0,
                    "stages": [],
                    "error": None,
                },
            )

        if state.stop.signum is not None:
            return "cancelled", "Stopped by a stop request."
        return "completed", None

    def _finish(
        self, state: CycleExecutionState, *, status: CycleExecutionStatus, error: str | None
    ) -> None:
        # Record where this run's events end before freeing the cycle: the next run
        # appends to the same file, and streams of this run must stop here.
        if state.events_path is not None:
            try:
                end = state.events_path.stat().st_size
            except OSError:
                end = state.events_start_offset_bytes
            with state.lock:
                state.events_end_offset_bytes = end
        # Free the cycle and update the registry before reporting `done`, so a client
        # that sees the run finish can start the cycle again right away.
        with self._states_lock:
            if self._active_by_cycle.get(state.cycle) == state.execution_id:
                self._active_by_cycle.pop(state.cycle, None)
            self._finished.append(state.execution_id)
            while len(self._finished) > self.max_finished:
                self._states.pop(self._finished.popleft(), None)
        state.mark_done(status=status, error=error)
