"""ReportPortal-compatible reporting routes: ``/api/v1/{project}/...`` and ``/api/v2/{project}/...``.

A ReportPortal agent pointed at this server (its endpoint set to the server's URL,
its API key set to ``TESTO_API_TOKEN``) reports here as it would to ReportPortal.
The calls and what they return follow ReportPortal's reporting API; what happens
to the data is :mod:`qasight.services.rp_compat`. This router is included last, so
QASight's own ``/api/v1/...`` routes always win a path both could match.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from qasight.config.errors import ConfigValidationError
from qasight.services.rp_compat import RpError, rp_store
from qasight_api.ui import ui_dir

router = APIRouter(prefix="/api/{version}/{project}", tags=["reportportal-compatible"])

_VERSION = Path(pattern="^v[12]$", description="ReportPortal API version the agent uses")
_PROJECT = Path(min_length=1, max_length=255, description="ReportPortal project name")
_MAX_JSON_BYTES = 32 * 1024 * 1024


def _error(exc: RpError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, content={"errorCode": exc.code, "message": str(exc)}
    )


async def _json(request: Request) -> Any:
    too_large = HTTPException(status_code=413, detail="request body is too large")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _MAX_JSON_BYTES:
        raise too_large
    # Read with a running cap, so a body without (or lying about) its length is
    # refused once it passes the limit instead of being held in memory whole.
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_JSON_BYTES:
            raise too_large
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        return json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc


def _object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    return body


@router.post("/launch", status_code=201)
async def start_launch(
    request: Request, version: str = _VERSION, project: str = _PROJECT
) -> JSONResponse:
    """Start a launch. Returns ``{"id": "<launch uuid>", "number": n}``."""
    body = _object(await _json(request))
    try:
        launch = await run_in_threadpool(rp_store.start_launch, project, body)
    except RpError as exc:
        return _error(exc)
    return JSONResponse(status_code=201, content={"id": launch.uuid, "number": launch.number})


@router.put("/launch/{launch_uuid}/finish")
async def finish_launch(
    request: Request,
    launch_uuid: str,
    version: str = _VERSION,
    project: str = _PROJECT,
) -> JSONResponse:
    """Finish a launch: what it reported is recorded as one run of its cycle."""
    body = _object(await _json(request))
    try:
        done = await run_in_threadpool(rp_store.finish_launch, project, launch_uuid, body)
    except RpError as exc:
        return _error(exc)
    except ConfigValidationError as exc:
        return JSONResponse(status_code=400, content={"errorCode": 4001, "message": str(exc)})
    base = str(request.base_url).rstrip("/")
    link = f"{base}/runs/{done.run_id}" if done.run_id and ui_dir() else f"{base}/api/v1/runs"
    return JSONResponse(content={"id": done.uuid, "number": done.number, "link": link})


@router.get("/launch/uuid/{launch_uuid}")
@router.get("/launch/{launch_uuid}")
async def get_launch(
    launch_uuid: str, version: str = _VERSION, project: str = _PROJECT
) -> JSONResponse:
    """What agents read back after starting or finishing a launch (its number, for links)."""
    try:
        launch = await run_in_threadpool(rp_store.get_launch, launch_uuid)
    except RpError as exc:
        return _error(exc)
    return JSONResponse(
        content={
            "id": launch.number,
            "uuid": launch.uuid,
            "number": launch.number,
            "name": launch.name,
            "status": launch.status,
            "mode": launch.mode,
            "qasightRunId": launch.run_id,
        }
    )


@router.post("/item", status_code=201)
async def start_root_item(
    request: Request, version: str = _VERSION, project: str = _PROJECT
) -> JSONResponse:
    """Start a top-level item (a suite, usually). Returns ``{"id": "<item uuid>"}``."""
    body = _object(await _json(request))
    try:
        item = await run_in_threadpool(rp_store.start_item, project, body, None)
    except RpError as exc:
        return _error(exc)
    return JSONResponse(status_code=201, content={"id": item.uuid})


@router.post("/item/{parent_uuid}", status_code=201)
async def start_child_item(
    request: Request,
    parent_uuid: str,
    version: str = _VERSION,
    project: str = _PROJECT,
) -> JSONResponse:
    """Start an item under *parent_uuid*: a test, or a nested step (``hasStats: false``)."""
    body = _object(await _json(request))
    try:
        item = await run_in_threadpool(rp_store.start_item, project, body, parent_uuid)
    except RpError as exc:
        return _error(exc)
    return JSONResponse(status_code=201, content={"id": item.uuid})


@router.put("/item/{item_uuid}")
async def finish_item(
    request: Request,
    item_uuid: str,
    version: str = _VERSION,
    project: str = _PROJECT,
) -> JSONResponse:
    """Finish an item with its status; a test shows in "Running now" right away."""
    body = _object(await _json(request))
    try:
        await run_in_threadpool(rp_store.finish_item, project, item_uuid, body)
    except RpError as exc:
        return _error(exc)
    return JSONResponse(
        content={"message": f"TestItem with ID = '{item_uuid}' successfully finished."}
    )


@router.post("/log/entry", status_code=201)
@router.post("/log", status_code=201)
async def save_log(
    request: Request, version: str = _VERSION, project: str = _PROJECT
) -> JSONResponse:
    """Save logs: one JSON entry, a JSON list, or multipart with ``json_request_part`` and files."""
    content_type = request.headers.get("content-type", "")
    files: dict[str, tuple[bytes, str | None]] = {}
    if content_type.startswith("multipart/"):
        form = await request.form(max_files=1000, max_fields=1000)
        entries: Any = []
        for key, value in form.multi_items():
            if key == "json_request_part":
                text = value if isinstance(value, str) else (await value.read()).decode("utf-8")
                try:
                    entries = json.loads(text)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc
            elif not isinstance(value, str):
                data = await value.read()
                files[value.filename or key] = (data, value.content_type)
        multipart = True
    else:
        entries = await _json(request)
        multipart = False
    single = isinstance(entries, dict)
    if single:
        entries = [entries]
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise HTTPException(status_code=400, detail="expected a log entry or a list of them")
    try:
        ids = await run_in_threadpool(rp_store.save_logs, project, entries, files)
    except RpError as exc:
        return _error(exc)
    if single and not multipart:
        return JSONResponse(status_code=201, content={"id": ids[0]})
    return JSONResponse(status_code=201, content={"responses": [{"id": i} for i in ids]})


info_router = APIRouter(tags=["reportportal-compatible"])


@info_router.get("/api/info")
def server_info() -> dict[str, Any]:
    """What ReportPortal clients ask a server first (its version decides the time format they send)."""
    from qasight import __version__

    return {
        "build": {"name": "QASight (ReportPortal-compatible reporting)", "version": "5.14.0"},
        "extensions": {"qasight": __version__},
    }
