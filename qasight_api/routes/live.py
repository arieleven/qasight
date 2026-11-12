"""Live test sessions: ``pytest --qasight-server`` reports each result as it happens.

See :mod:`qasight_api.live_runs`. Starting a session and sending results are writes
(token or a ``runner`` account); listing them is a read.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from qasight_api.live_runs import LiveSession, live_runs

router = APIRouter(prefix="/api/v1", tags=["live"])


class LiveRunStartRequest(BaseModel):
    cycle: str = Field(min_length=1, max_length=200)
    total: int | None = Field(default=None, ge=0, description="Tests collected, when known")
    branch: str | None = Field(default=None, max_length=200)
    commit: str | None = Field(default=None, max_length=64)


class LiveResult(BaseModel):
    test: str = Field(min_length=1, max_length=1000)
    outcome: Literal["passed", "failed", "skipped", "error"]
    message: str | None = Field(default=None, max_length=5000)


class LiveResultsRequest(BaseModel):
    results: list[LiveResult] = Field(max_length=1000)


class LiveFailureItem(BaseModel):
    test: str
    message: str


class LiveRunItem(BaseModel):
    id: str
    cycle: str
    started_at: float
    elapsed_s: float
    total: int | None
    done: int
    passed: int
    failed: int
    skipped: int
    errors: int
    branch: str | None
    commit: str | None
    current: str | None
    finished: bool
    failures: list[LiveFailureItem]


class LiveRunListResponse(BaseModel):
    items: list[LiveRunItem]


class LiveRunStarted(BaseModel):
    id: str


def _item(session: LiveSession, now: float) -> LiveRunItem:
    end = session.finished_at or now
    return LiveRunItem(
        id=session.id,
        cycle=session.cycle,
        started_at=session.started_at,
        elapsed_s=round(end - session.started_at, 1),
        total=session.total,
        done=session.done,
        passed=session.counts.get("passed", 0),
        failed=session.counts.get("failed", 0),
        skipped=session.counts.get("skipped", 0),
        errors=session.counts.get("error", 0),
        branch=session.branch,
        commit=session.commit,
        current=session.current,
        finished=session.finished_at is not None,
        failures=[LiveFailureItem(test=f.test, message=f.message) for f in session.failures],
    )


@router.get("/live-runs", response_model=LiveRunListResponse)
def list_live_runs() -> LiveRunListResponse:
    """Test sessions running now (and ones that ended in the last two minutes)."""
    now = time.time()
    return LiveRunListResponse(items=[_item(s, now) for s in live_runs.active(now=now)])


@router.post("/live-runs", response_model=LiveRunStarted, status_code=201)
def start_live_run(body: LiveRunStartRequest) -> LiveRunStarted:
    """Start a live session; send results to ``/live-runs/{id}/results``."""
    session = live_runs.start(body.cycle, total=body.total, branch=body.branch, commit=body.commit)
    return LiveRunStarted(id=session.id)


@router.post("/live-runs/{session_id}/results", status_code=204)
def add_live_results(session_id: str, body: LiveResultsRequest) -> Response:
    """Add finished tests to a live session."""
    results = [(r.test, r.outcome, r.message) for r in body.results]
    if live_runs.add_results(session_id, results) is None:
        raise HTTPException(status_code=404, detail="no such live session")
    return Response(status_code=204)


@router.post("/live-runs/{session_id}/finish", status_code=204)
def finish_live_run(session_id: str) -> Response:
    """Mark a live session ended; its report upload records it as a run."""
    if live_runs.finish(session_id) is None:
        raise HTTPException(status_code=404, detail="no such live session")
    return Response(status_code=204)
