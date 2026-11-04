from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from qasight.config.errors import ConfigDiscoveryError, ConfigError, ConfigValidationError
from qasight.config.loader import discover_and_load
from qasight.config.schema import PARALLEL_FRAMEWORKS
from qasight.history.read_model import latest_run_per_cycle
from qasight.history.views import CompletedRunView
from qasight_api.cycle_execution_manager import CycleExecutionManager
from qasight_api.dependencies import get_cycle_execution_manager
from qasight_api.execution_events import iter_execution_sse
from qasight_api.models import (
    AdhocExecutionRequest,
    CycleDetailResponse,
    CycleExecutionAcceptedResponse,
    CycleExecutionRequest,
    CycleExecutionStatusResponse,
    CycleLatestRun,
    CycleListResponse,
    CycleSummary,
    CycleTriggerSummary,
    StageSummary,
)
from qasight_api.security import read_config_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["cycles"])


def _load_config(config_path: str | None = None):
    try:
        return discover_and_load(config_path=read_config_path(config_path))
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=f"config error: {exc}") from exc


def _load_config_for_reads(config_path: str | None = None):
    """The config, or ``None`` when none is found: `qasight ui` over ingested runs needs no YAML.

    A ``config_path`` the caller named must exist, as before.
    """
    if config_path:
        return _load_config(config_path)
    try:
        return discover_and_load()
    except ConfigDiscoveryError:
        return None
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=f"config error: {exc}") from exc


_HISTORY_DESCRIPTION = (
    "Recorded runs only: this cycle isn't in qasight.yaml, so it can't be run from here."
)


def _latest_run(run: CompletedRunView | None) -> CycleLatestRun | None:
    if run is None:
        return None
    return CycleLatestRun(
        run_id=run.run_id,
        created_at=run.created_at,
        status=run.status.value if run.status is not None else None,
        returncode=run.returncode,
        health_pct=run.health_pct,
        total_tests=run.total_tests,
        passed=run.passed,
        failed=run.failed,
        branch=run.branch,
        commit=run.commit,
    )


def _history_summary(name: str, run: CompletedRunView) -> CycleSummary:
    return CycleSummary(
        name=name,
        description=_HISTORY_DESCRIPTION,
        stage_count=len(run.stage_health),
        equipment=sorted({str(s.get("framework")) for s in run.stage_health if s.get("framework")}),
        source="history",
        latest_run=_latest_run(run),
    )


def _optional_path(raw: str | None) -> Path | None:
    return Path(raw).expanduser().resolve() if raw else None


def _accepted(request: Request, execution_id: str) -> CycleExecutionAcceptedResponse:
    base = str(request.base_url).rstrip("/")
    return CycleExecutionAcceptedResponse(
        execution_id=execution_id,
        status="queued",
        events_url=f"{base}/api/v1/cycle-executions/{execution_id}/events",
        summary_url=f"{base}/api/v1/cycle-executions/{execution_id}",
    )


@router.get("/cycles", response_model=CycleListResponse)
def list_cycles(config_path: str | None = None) -> CycleListResponse:
    """Every cycle in the config (as ``qasight cycles list``), then cycles that only have recorded runs."""
    cfg = _load_config_for_reads(config_path)
    configured = cfg.cycles if cfg else {}
    latest = latest_run_per_cycle()
    items = [
        CycleSummary(
            name=cycle.name,
            description=cycle.description,
            stage_count=len(cycle.stages),
            equipment=sorted({stage.framework for stage in cycle.stages}),
            latest_run=_latest_run(latest.get(cycle.name)),
        )
        for cycle in configured.values()
    ]
    items += [_history_summary(name, run) for name, run in latest.items() if name not in configured]
    return CycleListResponse(
        items=items,
        config_path=str(cfg.source_path) if cfg and cfg.source_path else None,
    )


@router.get("/cycles/{cycle}", response_model=CycleDetailResponse)
def get_cycle(cycle: str, config_path: str | None = None) -> CycleDetailResponse:
    """Resolved view of one cycle (as ``qasight cycles show <name>``), or a recorded-only cycle."""
    cfg = _load_config_for_reads(config_path)
    configured = cfg.cycles if cfg else {}
    plan = configured.get(cycle)
    if plan is None:
        recorded = latest_run_per_cycle()
        run = recorded.get(cycle)
        if run is None:
            known = sorted({*configured, *recorded})
            raise HTTPException(
                status_code=404,
                detail=f"Unknown cycle: {cycle}. Available cycles: {', '.join(known) or '(none)'}",
            )
        return CycleDetailResponse(
            name=cycle,
            description=_HISTORY_DESCRIPTION,
            stages=[
                StageSummary(
                    name=str(stage.get("name") or ""),
                    equipment=str(stage.get("framework") or ""),
                    target_repo=run.target_repo or "",
                )
                for stage in run.stage_health
            ],
            source="history",
        )
    return CycleDetailResponse(
        name=plan.name,
        description=plan.description,
        stages=[
            StageSummary(
                name=stage.name,
                equipment=stage.framework,
                target_repo=str(stage.target_repo),
                args=list(stage.args),
                timeout_s=stage.timeout_s,
                # Only frameworks that act on it; showing "Workers 4" on a
                # single-process behave stage would claim parallelism it lacks.
                workers=stage.workers if stage.framework in PARALLEL_FRAMEWORKS else None,
            )
            for stage in plan.stages
        ],
        trigger=CycleTriggerSummary(
            paths=list(plan.trigger.paths), since_ref=plan.trigger.since_ref
        )
        if plan.trigger
        else None,
    )


@router.post(
    "/cycles/{cycle}/executions", response_model=CycleExecutionAcceptedResponse, status_code=202
)
def create_cycle_execution(
    cycle: str,
    payload: CycleExecutionRequest,
    request: Request,
    manager: CycleExecutionManager = Depends(get_cycle_execution_manager),
) -> CycleExecutionAcceptedResponse:
    """Start a run of ``cycle``; with ``tests`` or ``rerun_failed``, only those tests."""
    if payload.rerun_failed:
        from qasight.services.test_selection import SelectionError, from_run_failures

        try:  # checked now so the caller hears about it, not the event stream
            from_run_failures(payload.rerun_failed.strip())
        except SelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        state = manager.create_execution(
            cycle=str(cycle),
            config_path=_optional_path(payload.config_path),
            artifacts_root_override=_optional_path(payload.artifacts_root),
            persist=bool(payload.persist),
            force=bool(payload.force),
            fail_fast=bool(payload.fail_fast),
            reporter_override=list(payload.reporter_override)
            if payload.reporter_override
            else None,
            report_db=bool(payload.report_db),
            async_report_db=bool(payload.async_report_db),
            workers_override=payload.workers_override,
            stream=bool(payload.stream),
            ci=True,
            tests=[t for t in payload.tests if t.strip()],
            rerun_failed=(payload.rerun_failed or "").strip() or None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        # Broad on purpose: existing API contract maps any start-up failure to a structured 400.
        logger.warning("could not start cycle %s", cycle, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _accepted(request, state.execution_id)


@router.post("/adhoc-executions", response_model=CycleExecutionAcceptedResponse, status_code=202)
def create_adhoc_execution(
    payload: AdhocExecutionRequest,
    request: Request,
    manager: CycleExecutionManager = Depends(get_cycle_execution_manager),
) -> CycleExecutionAcceptedResponse:
    """Run one framework directly as a one-stage ``adhoc`` cycle.

    Same engine, persistence and event stream as a named cycle: progress and
    status live under ``/cycle-executions/{execution_id}``.
    """
    try:
        state = manager.create_adhoc_execution(
            framework=payload.framework,
            target_repo=Path(payload.target_repo),
            args=payload.args,
            timeout_s=payload.timeout_s,
            extra_env=payload.extra_env,
            config_path=_optional_path(payload.config_path),
            artifacts_root_override=_optional_path(payload.artifacts_root),
            persist=bool(payload.persist),
            report_db=bool(payload.report_db),
        )
    except ConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _accepted(request, state.execution_id)


def _execution_not_found(execution_id: str, manager: CycleExecutionManager) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=(
            f"Execution not found: {execution_id}. Only running executions and the last "
            f"{manager.max_finished} finished ones are kept in memory; completed runs "
            "remain in the run history (/api/v1/runs)."
        ),
    )


@router.get("/cycle-executions/{execution_id}", response_model=CycleExecutionStatusResponse)
def get_cycle_execution(
    execution_id: str,
    manager: CycleExecutionManager = Depends(get_cycle_execution_manager),
) -> CycleExecutionStatusResponse:
    state = manager.get(execution_id)
    if state is None:
        raise _execution_not_found(execution_id, manager)
    with state.lock:
        return CycleExecutionStatusResponse(
            execution_id=execution_id,
            cycle=state.cycle,
            status=state.status,
            artifacts_root=str(state.artifacts_root) if state.artifacts_root else None,
            events_path=str(state.events_path) if state.events_path else None,
            plan_result_path=str(state.plan_result_path) if state.plan_result_path else None,
            error=state.error,
        )


@router.get("/cycle-executions/{execution_id}/events")
def stream_cycle_execution_events(
    execution_id: str,
    manager: CycleExecutionManager = Depends(get_cycle_execution_manager),
) -> StreamingResponse:
    if manager.get(execution_id) is None:
        raise _execution_not_found(execution_id, manager)
    return StreamingResponse(
        iter_execution_sse(manager, execution_id),
        media_type="text/event-stream",
    )


@router.post("/cycle-executions/{execution_id}/cancel", response_model=CycleExecutionStatusResponse)
def cancel_cycle_execution(
    execution_id: str,
    manager: CycleExecutionManager = Depends(get_cycle_execution_manager),
) -> CycleExecutionStatusResponse:
    """Stop a run: a queued one never starts; a running one's stages get SIGTERM.

    Send it again to kill stages that ignore SIGTERM. The run is still recorded, as
    failed with ``interrupted_by`` set; its status here becomes ``cancelled``.
    """
    state = manager.cancel(execution_id)
    if state is None:
        raise _execution_not_found(execution_id, manager)
    return get_cycle_execution(execution_id, manager)
