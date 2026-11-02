"""Top-level orchestrator: run plan stages, emit events, aggregate results.

Stages run one at a time by default. A cycle with ``max_parallel_stages: N`` runs up
to N stages at once on a thread pool (each stage is a subprocess, so threads only
wait on it). Either way, events and the renderer are only touched from the calling
thread, so the NDJSON stream and the terminal stay consistent. See
``docs/Specs & ADRs/Scale Runs and Stages - 2026-10-24.md``.
"""

from __future__ import annotations

import contextvars
import dataclasses
import json
import logging
import os
import shutil
import signal
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Protocol

from testo_core.config.schema import Plan, Stage
from testo_core.engine import interrupt
from testo_core.engine.events import (
    EngineEvent,
    PlanFinished,
    PlanStarted,
    StageFinished,
    StageStarted,
)
from testo_core.engine.executor import run_stage
from testo_core.engine.exit_codes import EngineExitCode, classify_exit_code
from testo_core.engine.result import PlanResult, StageResult
from testo_core.paths import plan_artifacts_dir

logger = logging.getLogger(__name__)


class _EventSink(Protocol):
    """Anything with a ``handle(EngineEvent)`` method qualifies as a renderer."""

    wants_streaming: bool

    def handle(self, event: EngineEvent) -> None: ...


def run_plan(
    plan: Plan,
    *,
    renderer: _EventSink,
    artifacts_root: Path | None = None,
    parent_env: Mapping[str, str] | None = None,
    persist: bool = True,
    fail_fast: bool = False,
    run_metadata: Mapping[str, str | None] | None = None,
    coverage_reports: Sequence[Path] = (),
) -> PlanResult:
    """Execute every stage in ``plan``: in order, or up to ``plan.max_parallel_stages`` at once.

    When *persist* is ``True``, the :mod:`testo_core.persistence` composite
    backend writes ``plan_result.json`` and (when the DB layer is available)
    upserts a :class:`~testo_core.repository.models.RunRecord`.

    When *fail_fast* is ``True``, a stage finishing with a non-zero returncode
    aborts the remaining stages: a ``plan_aborted`` NDJSON event is written
    (see ``docs/CLI Commands/Troubleshooting and Error Codes.md``) before the
    terminal ``plan_finished`` event.

    *run_metadata* is carried on ``PlanResult.extra["run_metadata"]`` and merged
    into the run record by the DB backend. *coverage_reports* (Cobertura, JaCoCo,
    LCOV or Go cover profiles the stages wrote) are read once every stage has
    finished and their line coverage is stored the same way.
    """
    artifacts_root = (artifacts_root or Path("artifacts")).expanduser().resolve()
    plan_artifacts = plan_artifacts_dir(artifacts_root, plan.name)
    plan_artifacts.mkdir(parents=True, exist_ok=True)
    events_path = plan_artifacts / "events.ndjson"
    parent_env = parent_env if parent_env is not None else dict(os.environ)

    started_at = time.time()
    renderer.handle(PlanStarted(plan=plan))

    max_parallel = max(1, plan.max_parallel_stages)
    # Live output from several stages at once would interleave into noise, so parallel
    # stages are buffered: each stage's panel (with its output tail) prints when it ends.
    on_chunk = _make_on_chunk(renderer, stream=renderer.wants_streaming and max_parallel == 1)

    with _NdjsonRecorder(events_path) as recorder:
        recorder.write(
            {"event": "plan_started", "plan": plan.name, "stage_count": len(plan.stages)}
        )

        # Events and the renderer are only touched from this thread; with parallel stages,
        # worker threads only run `_execute_stage` and hand back its result.
        def start(idx: int, stage: Stage) -> None:
            renderer.handle(
                StageStarted(stage=stage, stage_index=idx, stage_count=len(plan.stages))
            )
            recorder.write(
                {
                    "event": "stage_started",
                    "stage": stage.name,
                    "framework": stage.framework,
                    "index": idx,
                    "count": len(plan.stages),
                }
            )

        def execute(stage: Stage) -> StageResult:
            return _execute_stage(
                stage,
                plan=plan,
                artifacts_root=artifacts_root,
                parent_env=parent_env,
                on_chunk=on_chunk(stage.name),
            )

        def finish(stage_result: StageResult) -> None:
            renderer.handle(StageFinished(result=stage_result))
            recorder.write(_stage_finished_payload(stage_result))

        def abort(completed: int) -> None:
            stopped_by = interrupt.requested()
            scope = interrupt.current()
            reason = "fail_fast" if stopped_by is None else "stopped" if scope.reason else "signal"
            recorder.write(
                {
                    "event": "plan_aborted",
                    "plan": plan.name,
                    "reason": reason,
                    "completed_stages": completed,
                    **({} if stopped_by is None else {"signal": interrupt.signal_name(stopped_by)}),
                }
            )

        with interrupt.stop_on_signals():
            if max_parallel == 1:
                stage_results = _run_sequential(
                    plan,
                    start=start,
                    execute=execute,
                    finish=finish,
                    abort=abort,
                    fail_fast=fail_fast,
                )
            else:
                stage_results = _run_parallel(
                    plan,
                    max_parallel=max_parallel,
                    start=start,
                    execute=execute,
                    finish=finish,
                    abort=abort,
                    fail_fast=fail_fast,
                )
        stopped_by = interrupt.requested()

        _remove_stale_stage_dirs(plan_artifacts, ran={s.stage_name for s in stage_results})
        finished_at = time.time()
        rcs = [s.returncode for s in stage_results]
        exit_code = classify_exit_code(
            rcs,
            infra_error=None,
            internal_failure=any(s.internal_failure for s in stage_results),
        )
        metadata = dict(run_metadata) if run_metadata else {}
        if stopped_by is not None:
            # Recorded (as failed, with the signal in its metadata), then exit 130 / 143.
            exit_code = (
                EngineExitCode.TERMINATED
                if stopped_by == signal.SIGTERM
                else EngineExitCode.INTERRUPTED
            )
            metadata["interrupted_by"] = interrupt.stop_reason()
        target_repos = {s.target_repo for s in stage_results if s.target_repo is not None}
        target_repo = next(iter(target_repos)) if len(target_repos) == 1 else None
        plan_result = PlanResult(
            plan_name=plan.name,
            started_at=started_at,
            finished_at=finished_at,
            duration_s=_plan_duration(
                finished_at - started_at, stage_results, parallel=max_parallel > 1
            ),
            stages=tuple(stage_results),
            aggregate_returncode=max(rcs, default=0),
            exit_code=exit_code,
            target_repo=target_repo,
            extra={"run_metadata": metadata} if metadata else {},
        )

        recorder.write(
            {
                "event": "plan_finished",
                "plan": plan_result.plan_name,
                "aggregate_returncode": plan_result.aggregate_returncode,
                "exit_code": int(plan_result.exit_code),
                "duration_s": plan_result.duration_s,
            }
        )

    renderer.handle(PlanFinished(result=plan_result))

    # Before anything is recorded or reported: hide secrets a test printed into its
    # results or logs (TESTO_REDACT=0 turns it off; TESTO_REDACT_PATTERNS adds your own).
    from testo_core.security.redaction import redact_run_artifacts

    redact_run_artifacts(plan_artifacts)

    if coverage_reports:
        plan_result = _with_coverage(plan_result, coverage_reports)

    if persist:
        from testo_core.persistence import composite_backend

        backend = composite_backend(artifacts_root=artifacts_root)
        run_id = backend.persist(plan_result)
        if run_id:
            plan_result = dataclasses.replace(
                plan_result, extra={**plan_result.extra, "run_id": run_id}
            )

    return plan_result


def _with_coverage(plan_result: PlanResult, reports: Sequence[Path]) -> PlanResult:
    from testo_core.reporting.coverage_import import CoverageFormatError, parse_coverage

    present = [p for p in reports if p.is_file()]
    if not present:
        logger.warning("coverage: no report at %s", ", ".join(str(p) for p in reports))
        return plan_result
    try:
        summary = parse_coverage(present)
    except CoverageFormatError as exc:
        logger.warning("coverage: %s", exc)
        return plan_result
    before = plan_result.extra.get("run_metadata")
    metadata = {**(before if isinstance(before, dict) else {}), **summary.as_metadata()}
    return dataclasses.replace(plan_result, extra={**plan_result.extra, "run_metadata": metadata})


def _remove_stale_stage_dirs(plan_dir: Path, *, ran: set[str]) -> None:
    """Delete stage folders this run didn't write (a stage removed, renamed, skipped by
    ``if:`` or by ``fail_fast``), so the reports, archive and history snapshot that read
    ``plan_dir`` after the run only see this run's stages."""
    keep = {name.casefold() for name in ran}
    for child in plan_dir.iterdir():
        if not child.is_dir() or child.is_symlink() or child.name.casefold() in keep:
            continue
        # Only folders the executor makes; anything else under the cycle folder stays.
        if not ((child / "run.log").is_file() or (child / "allure-results").is_dir()):
            continue
        try:
            shutil.rmtree(child)
        except OSError:
            logger.warning("could not remove stale stage folder %s", child, exc_info=True)


def _run_sequential(
    plan: Plan,
    *,
    start: Callable[[int, Stage], None],
    execute: Callable[[Stage], StageResult],
    finish: Callable[[StageResult], None],
    abort: Callable[[int], None],
    fail_fast: bool,
) -> list[StageResult]:
    """The default: one stage at a time, in config order, on the calling thread."""
    results: list[StageResult] = []
    for idx, stage in enumerate(plan.stages, start=1):
        if interrupt.requested() is not None:
            abort(idx - 1)
            break
        start(idx, stage)
        result = execute(stage)
        results.append(result)
        finish(result)
        if fail_fast and result.returncode != 0:
            abort(idx)
            break
    return results


def _run_parallel(
    plan: Plan,
    *,
    max_parallel: int,
    start: Callable[[int, Stage], None],
    execute: Callable[[Stage], StageResult],
    finish: Callable[[StageResult], None],
    abort: Callable[[int], None],
    fail_fast: bool,
) -> list[StageResult]:
    """Run up to ``max_parallel`` stages at once; results come back in config order.

    Stages start in config order as slots free up, and finish in whatever order they
    end. With ``fail_fast``, the first failure stops new stages from starting; the
    ones already running finish (their subprocesses are not killed), then
    ``plan_aborted`` is written.
    """
    pending = deque(enumerate(plan.stages, start=1))
    running: dict[Future[StageResult], int] = {}
    results: dict[int, StageResult] = {}
    aborted = False
    with ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="testo-stage") as pool:
        while pending or running:
            if interrupt.requested() is not None:
                aborted = True
            while pending and not aborted and len(running) < max_parallel:
                idx, stage = pending.popleft()
                start(idx, stage)
                # A copy of this thread's context per stage, so the stage sees the run's
                # stop scope (interrupt.scoped) and the Stop button reaches it.
                ctx = contextvars.copy_context()
                running[pool.submit(ctx.run, execute, stage)] = idx
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            # Several can finish in one wake-up; report them in config order.
            for future in sorted(done, key=running.__getitem__):
                idx = running.pop(future)
                result = future.result()  # `_execute_stage` never raises
                results[idx] = result
                finish(result)
                if fail_fast and result.returncode != 0:
                    aborted = True
    if aborted:
        abort(len(results))
    return [results[idx] for idx in sorted(results)]


def _execute_stage(
    stage: Stage,
    *,
    plan: Plan,
    artifacts_root: Path,
    parent_env: Mapping[str, str],
    on_chunk: Callable[[bytes], None] | None,
) -> StageResult:
    try:
        return run_stage(
            stage,
            plan_name=plan.name,
            artifacts_root=artifacts_root,
            parent_env=parent_env,
            on_chunk=on_chunk,
            quarantine=plan.quarantine,
        )
    except Exception as exc:
        # Broad on purpose: an engine bug becomes an internal-failure result (exit 4)
        # so later stages, persistence and reporters still run.
        logger.exception("stage %s crashed inside the engine", stage.name)
        return _internal_failure_result(stage=stage, exc=exc)


def _stage_finished_payload(stage_result: StageResult) -> dict[str, object]:
    return {
        "event": "stage_finished",
        "stage": stage_result.stage_name,
        "framework": stage_result.framework,
        "returncode": stage_result.returncode,
        "duration_s": stage_result.duration_s,
        "log_path": str(stage_result.log_path) if stage_result.log_path else None,
        "timed_out": stage_result.timed_out,
        "error": stage_result.error,
        "internal_failure": stage_result.internal_failure,
        "quarantined": list(stage_result.quarantined),
        "waived_returncode": stage_result.waived_returncode,
        "crash_kind": stage_result.crash_kind,
        "crash_detail": stage_result.crash_detail,
    }


def _plan_duration(wall_s: float, stage_results: list[StageResult], *, parallel: bool) -> float:
    """Plan duration, never shorter than its stages say they took.

    ``junit`` stages report the test time recorded in their reports rather than the
    milliseconds the import took, so the wall clock alone can undercount. Sequential
    stages add up; parallel ones overlap, so only the longest counts.
    """
    durations = [s.duration_s for s in stage_results]
    stages_s = max(durations, default=0.0) if parallel else sum(durations)
    return max(wall_s, stages_s)


def _make_on_chunk(renderer: _EventSink, *, stream: bool):
    """Build a per-stage chunk callback for the executor.

    When streaming, every byte goes through :class:`StageOutputChunk`; the
    renderer prints to the terminal as bytes arrive.  When buffered, the
    callback is a no-op (the log buffer still tees to disk).
    """
    if not stream:

        def _no_op(stage_name: str):  # noqa: ARG001
            return None

        return _no_op

    from testo_core.engine.events import StageOutputChunk

    def factory(stage_name: str):
        def cb(chunk: bytes) -> None:
            renderer.handle(StageOutputChunk(stage_name=stage_name, chunk=chunk))

        return cb

    return factory


class _NdjsonRecorder:
    """Append-only NDJSON file (one event per line)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def write(self, payload: dict[str, object]) -> None:
        self._fh.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))
        self._fh.write("\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.flush()
            self._fh.close()
        except OSError:  # pragma: no cover
            pass

    def __enter__(self) -> _NdjsonRecorder:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _internal_failure_result(*, stage, exc: Exception) -> StageResult:  # type: ignore[no-untyped-def]
    now = time.time()
    return StageResult(
        stage_name=stage.name,
        framework=stage.framework,
        tier=stage.tier,
        returncode=4,
        started_at=now,
        finished_at=now,
        duration_s=0.0,
        log_path=None,
        artifacts_dir=Path("."),
        command=(),
        output_tail="",
        timed_out=False,
        error=f"internal error: {exc}",
        internal_failure=True,
    )
