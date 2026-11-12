"""Release scorecard: tool switches, a version's scores, the Markdown summary, scans and results from clients.

See :mod:`qasight.services.scorecard`. Switching a tool, recording a result and
starting a scan are writes, so they need the API token (or a ``runner``
account) like starting a cycle does. A scan runs tools on this machine, like a
cycle runs its commands.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from qasight.services.scorecard.report import build_scorecard, projects, to_markdown
from qasight.services.scorecard.scan import ScanError
from qasight.services.scorecard.store import ScorecardError, ScorecardStore
from qasight.services.scorecard.tools import CATEGORIES, TOOLS, TOOLS_BY_ID, Tool
from qasight_api.models import (
    ScorecardFindingItem,
    ScorecardMarkdownResponse,
    ScorecardProjectsResponse,
    ScorecardResponse,
    ScorecardResultRequest,
    ScorecardScanRequest,
    ScorecardScanResponse,
    ScorecardToolItem,
    ScorecardToolScoreItem,
    ScorecardToolsResponse,
    ScorecardToolUpdate,
)
from qasight_api.scorecard_scans import ScanBusyError, ScorecardScanManager, get_scan_manager

router = APIRouter(prefix="/api/v1", tags=["scorecard"])


def _tool_item(tool: Tool, enabled: bool) -> ScorecardToolItem:
    return ScorecardToolItem(
        id=tool.id,
        name=tool.name,
        category=tool.category,
        category_name=CATEGORIES[tool.category],
        tagline=tool.tagline,
        how=tool.how,
        homepage=tool.homepage,
        kind=tool.kind,
        report=tool.report,
        languages=sorted(tool.languages),
        default_on=tool.default_on,
        enabled=enabled,
        installed=tool.installed() if tool.binary else None,
        install=tool.install,
    )


@router.get("/scorecard/tools", response_model=ScorecardToolsResponse)
def list_scorecard_tools() -> ScorecardToolsResponse:
    """Every tool the scorecard knows, switched on or off, and whether this server can run it."""
    enabled = ScorecardStore().enabled()
    return ScorecardToolsResponse(items=[_tool_item(t, enabled[t.id]) for t in TOOLS])


@router.put("/scorecard/tools/{tool_id}", response_model=ScorecardToolItem)
def set_scorecard_tool(tool_id: str, body: ScorecardToolUpdate) -> ScorecardToolItem:
    """Switch a tool on or off: scans skip it and scores leave it out."""
    if tool_id not in TOOLS_BY_ID:
        raise HTTPException(status_code=404, detail=f"No tool {tool_id!r}.")
    store = ScorecardStore()
    store.set_enabled(tool_id, body.enabled)
    return _tool_item(TOOLS_BY_ID[tool_id], body.enabled)


@router.get("/scorecard/projects", response_model=ScorecardProjectsResponse)
def list_scorecard_projects() -> ScorecardProjectsResponse:
    """Every scored project, the latest first, with its versions and their scores."""
    return ScorecardProjectsResponse.model_validate({"items": [asdict(p) for p in projects()]})


@router.get("/scorecard", response_model=ScorecardResponse)
def get_scorecard(project: str | None = None, version: str | None = None) -> ScorecardResponse:
    """A version's scorecard (default: the latest scored), compared with the version before."""
    card = build_scorecard(project or None, version or None)
    if card is None:
        return ScorecardResponse(status="empty", project=project, version=version)
    return ScorecardResponse.model_validate({"status": "available", **asdict(card)})


@router.get("/scorecard/markdown", response_model=ScorecardMarkdownResponse)
def get_scorecard_markdown(
    project: str | None = None, version: str | None = None
) -> ScorecardMarkdownResponse:
    """The scorecard as a Markdown document."""
    card = build_scorecard(project or None, version or None)
    if card is None:
        raise HTTPException(
            status_code=404, detail="Nothing scored for that project and version yet."
        )
    return ScorecardMarkdownResponse(markdown=to_markdown(card))


@router.post("/scorecard/results", response_model=ScorecardToolScoreItem)
def record_scorecard_result(body: ScorecardResultRequest) -> ScorecardToolScoreItem:
    """Record one tool's result that a client parsed (``qasight scorecard import|scan --server``)."""
    try:
        stored = ScorecardStore().record_raw(
            project=body.project,
            version=body.version,
            tool=body.tool,
            score=body.score,
            summary=body.summary,
            metrics=body.metrics,
            counts=body.counts,
            findings=[f.model_dump() for f in body.findings],
            commit_sha=body.commit_sha,
            source="server",
        )
    except ScorecardError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    tool = TOOLS_BY_ID[stored.tool]
    return ScorecardToolScoreItem(
        tool=stored.tool,
        name=tool.name,
        category=tool.category,
        enabled=ScorecardStore().enabled()[stored.tool],
        score=stored.score,
        summary=stored.summary,
        metrics=stored.metrics,
        counts=stored.counts,
        findings=[ScorecardFindingItem.model_validate(f) for f in stored.findings],
        recorded_at=stored.recorded_at,
        source=stored.source,
        commit_sha=stored.commit_sha,
    )


@router.post("/scorecard/scans", response_model=ScorecardScanResponse, status_code=202)
def start_scorecard_scan(
    body: ScorecardScanRequest,
    manager: ScorecardScanManager = Depends(get_scan_manager),
) -> ScorecardScanResponse:
    """Scan a folder on this server or a git URL (cloned shallow) with the switched-on tools."""
    try:
        state = manager.start(body)
    except ScanBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ScanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ScorecardScanResponse.model_validate(state.to_dict())


@router.get("/scorecard/scans/{scan_id}", response_model=ScorecardScanResponse)
def get_scorecard_scan(
    scan_id: str, manager: ScorecardScanManager = Depends(get_scan_manager)
) -> ScorecardScanResponse:
    """A scan's progress: each tool queued, running, done or failed."""
    state = manager.get(scan_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="No such scan (scans are kept in memory until the server restarts).",
        )
    return ScorecardScanResponse.model_validate(state.to_dict())
