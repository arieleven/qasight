from __future__ import annotations

import logging
import mimetypes
import threading
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from qasight import __version__
from qasight.config.history_section import SERVER_PRUNE_INTERVAL_S
from qasight.paths import STATIC_HISTORY_ROOT
from qasight_api.report_origin import route_report_request
from qasight_api.routes.ai import router as ai_router
from qasight_api.routes.analytics import router as analytics_router
from qasight_api.routes.ask import router as ask_router
from qasight_api.routes.auth import router as auth_router
from qasight_api.routes.badge import router as badge_router
from qasight_api.routes.cycles import router as cycles_router
from qasight_api.routes.dashboard import router as dashboard_router
from qasight_api.routes.health import router as health_router
from qasight_api.routes.history import router as history_router
from qasight_api.routes.ingest import router as ingest_router
from qasight_api.routes.project_health import router as project_health_router
from qasight_api.routes.quarantine import router as quarantine_router
from qasight_api.routes.report import router as report_router
from qasight_api.routes.resources import router as resources_router
from qasight_api.routes.scorecard import router as scorecard_router
from qasight_api.routes.storage import router as storage_router
from qasight_api.routes.triage import router as triage_router
from qasight_api.routes.version import router as version_router
from qasight_api.security import (
    accounts_enabled,
    add_security_headers,
    audit_write,
    cors_settings,
    guard_host,
    guard_mutating_request,
    guard_read_request,
    is_loopback_host,
)
from qasight_api.ui import SpaStaticFiles, ui_dir

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Runs left "RUNNING" by a crashed/reloaded process would otherwise sit
    # stuck forever in the UI history; mark them FAILED so the run list stays honest.
    from qasight.history.maintenance import cleanup_orphaned_runs

    try:
        n = cleanup_orphaned_runs(note="Orphaned by API server restart")
        if n:
            logger.warning("Cleaned up %s orphaned RUNNING run(s) on startup.", n)
    except Exception:
        # Broad on purpose: DB URL, driver and connection errors must not block API startup.
        logger.warning("orphaned-run cleanup skipped (DB unavailable?)", exc_info=True)

    # A run this server (or a CLI on this machine) was executing when it died left its
    # marker in the cycle's lock file; record it as failed rather than lose it.
    try:
        n = _recover_unfinished_runs()
        if n:
            logger.warning("Recorded %s run(s) that stopped without finishing.", n)
    except Exception:
        # Broad on purpose: like the cleanup above, a DB or disk problem must not block startup.
        logger.warning("unfinished-run recovery skipped", exc_info=True)

    # With TESTO_RUN_QUEUE=db, runs that were still waiting when the last process
    # stopped are queued again (no-op with the default in-memory queue).
    from qasight_api.dependencies import get_cycle_execution_manager

    try:
        n = get_cycle_execution_manager().recover_queued()
        if n:
            logger.warning("Re-queued %s run(s) left waiting by the previous process.", n)
    except Exception:
        # Broad on purpose: like the cleanup above, a DB problem must not block startup.
        logger.warning("queued-run recovery skipped (DB unavailable?)", exc_info=True)

    # TESTO_AUTH=accounts with TESTO_ADMIN_USER / TESTO_ADMIN_PASSWORD and no accounts yet:
    # create that admin (a fresh `qasight server init` server).
    from qasight.accounts import ensure_first_admin

    try:
        created = ensure_first_admin()
        if created:
            logger.warning("Created the first admin account %r from TESTO_ADMIN_USER.", created)
    except Exception:
        # Broad on purpose: a bad name or password, or a DB problem, must not block startup.
        logger.warning("first admin from TESTO_ADMIN_USER not created", exc_info=True)

    # history.retention in qasight.yaml: prune now and every few hours while the
    # server runs. Without the section this starts nothing.
    stop_retention = _start_retention_timer()
    try:
        yield
    finally:
        stop_retention()


def _recover_unfinished_runs() -> int:
    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load
    from qasight.history.maintenance import recover_unfinished_runs

    try:
        artifacts_root = discover_and_load().defaults.artifacts_root
    except ConfigError:
        return 0  # no config here (ingest-only server): this server runs nothing
    return recover_unfinished_runs(artifacts_root)


# How often the server applies history.retention (also shown by GET /api/v1/run-settings).
RETENTION_INTERVAL_S = SERVER_PRUNE_INTERVAL_S


def _start_retention_timer() -> Callable[[], None]:
    """Prune expired history on a daemon thread; returns a function that stops it.

    The limits are ``history.retention`` in ``qasight.yaml`` with
    ``TESTO_RETENTION_DAYS`` / ``TESTO_RETENTION_RUNS_PER_CYCLE`` on top, so a server
    without a config file (the Docker team server) is limited by its environment.
    """
    from qasight.config.errors import ConfigError
    from qasight.history.retention import effective_history

    try:
        retention = effective_history().retention
    except ConfigError:
        logger.warning("history retention off: bad TESTO_RETENTION_* value", exc_info=True)
        return lambda: None
    if not retention.enabled:
        return lambda: None

    from qasight.history.retention import prune_history, prune_report_archives

    stop = threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                report = prune_history(retention)
                if report.runs_deleted:
                    logger.info("history retention deleted %s run(s)", report.runs_deleted)
                prune_report_archives(retention)
            except Exception:
                # Broad on purpose: a DB hiccup must not kill the timer or the server.
                logger.warning("history retention run failed", exc_info=True)
            stop.wait(RETENTION_INTERVAL_S)

    threading.Thread(target=_loop, name="testo-history-retention", daemon=True).start()
    logger.info(
        "history retention on: days=%s runs_per_cycle=%s, every %s h",
        retention.days,
        retention.runs_per_cycle,
        RETENTION_INTERVAL_S // 3600,
    )
    return stop.set


def _json_safe_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Validation errors as JSON: a raw ``bytes`` body or an exception in ``ctx``
    would otherwise make the 422 handler itself fail with a 500."""

    def safe(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [safe(v) for v in value]
        return str(value)

    return [safe(dict(error)) for error in errors]


def create_app() -> FastAPI:
    app = FastAPI(
        title="QASight API",
        version=__version__,
        lifespan=_lifespan,
        dependencies=[Depends(guard_mutating_request)],
    )

    allowed_origins, allow_credentials = cors_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(ai_router)
    app.include_router(ask_router)
    app.include_router(auth_router)
    app.include_router(badge_router)
    app.include_router(cycles_router)
    app.include_router(history_router)
    app.include_router(analytics_router)
    app.include_router(dashboard_router)
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(project_health_router)
    app.include_router(triage_router)
    app.include_router(quarantine_router)
    app.include_router(report_router)
    app.include_router(resources_router)
    app.include_router(scorecard_router)
    app.include_router(storage_router)
    app.include_router(version_router)

    STATIC_HISTORY_ROOT.mkdir(parents=True, exist_ok=True)
    # Stage logs (<stage>/run.log) open in the browser instead of downloading.
    mimetypes.add_type("text/plain", ".log")
    app.mount("/history", StaticFiles(directory=STATIC_HISTORY_ROOT), name="history")

    built_ui = ui_dir()
    if built_ui is not None:
        # Last, so every API route and /history wins over the UI's catch-all.
        app.mount("/", SpaStaticFiles(directory=built_ui, html=True), name="ui")

    # Innermost, so it sees the final status and the principal the guards found.
    @app.middleware("http")
    async def audit_writes(request: Request, call_next):  # type: ignore[no-redef]
        response = await call_next(request)
        if accounts_enabled() and request.method not in ("GET", "HEAD", "OPTIONS"):
            await run_in_threadpool(audit_write, request, response.status_code)
        return response

    @app.middleware("http")
    async def protect_reads(request: Request, call_next):  # type: ignore[no-redef]
        # A middleware, not a dependency, so the static mounts are covered too. In a
        # thread: with accounts on, checking the session reads the database.
        refusal = guard_host(request)
        if refusal is None:
            refusal = await run_in_threadpool(route_report_request, request)
        if refusal is None:
            refusal = await run_in_threadpool(guard_read_request, request)
        return refusal if refusal is not None else await call_next(request)

    # Outside protect_reads, so its 401s and redirects get the headers too.
    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-redef]
        response = await call_next(request)
        add_security_headers(request, response)
        return response

    # Added last so it runs first: protect_reads' 401 carries the request id.
    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):  # type: ignore[no-redef]
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:  # type: ignore[no-redef]
        status_map = {
            400: "invalid_input",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "domain_failure",
            422: "invalid_input",
            503: "infra_failure",
        }
        code = status_map.get(exc.status_code, "internal_error")
        message = str(exc.detail)
        details = None
        if isinstance(exc.detail, dict):
            maybe_code = exc.detail.get("code")
            if isinstance(maybe_code, str):
                code = maybe_code
            maybe_message = exc.detail.get("message")
            if isinstance(maybe_message, str):
                message = maybe_message
            maybe_details = exc.detail.get("details")
            if isinstance(maybe_details, dict):
                details = maybe_details
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "details": details,
                },
                "request_id": getattr(request.state, "request_id", str(uuid4())),
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:  # type: ignore[no-redef]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_input",
                    "message": "Request validation failed.",
                    "details": {"errors": _json_safe_errors(exc.errors())},
                },
                "request_id": getattr(request.state, "request_id", str(uuid4())),
            },
        )

    return app


app = create_app()


def run(argv: list[str] | None = None) -> int:
    """Console-script entry-point for ``qasight-api``.

    Launches the FastAPI app with ``uvicorn``. Both packages are optional
    extras; if ``uvicorn`` is missing, a friendly message is printed instead
    of an opaque ``ModuleNotFoundError``.
    """
    import os
    import sys

    try:
        import uvicorn  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        sys.stderr.write("uvicorn is not installed. Run `pip install qasight[api]` first.\n")
        return 1

    host = os.environ.get("TESTO_API_HOST", "127.0.0.1")
    port = int(os.environ.get("TESTO_API_PORT", "8000"))
    reload = os.environ.get("TESTO_API_RELOAD", "0") in ("1", "true", "True")
    if not is_loopback_host(host) and not os.environ.get("TESTO_API_TOKEN", "").strip():
        if os.environ.get("TESTO_ALLOW_UNAUTHENTICATED", "0") not in ("1", "true", "True"):
            sys.stderr.write(
                f"Refusing to bind {host} without TESTO_API_TOKEN: anyone who can reach this "
                "port could start runs, i.e. execute commands on this machine. Set "
                "TESTO_API_TOKEN, bind 127.0.0.1, or set TESTO_ALLOW_UNAUTHENTICATED=1 "
                "if the network is already trusted.\n"
            )
            return 2
        logger.warning(
            "qasight-api is binding %s without TESTO_API_TOKEN (TESTO_ALLOW_UNAUTHENTICATED=1): "
            "anyone who can reach this port can start runs.",
            host,
        )
    if (
        os.environ.get("TESTO_AUTH", "token").strip().lower() == "accounts"
        and not os.environ.get("TESTO_API_TOKEN", "").strip()
    ):
        sys.stderr.write(
            "TESTO_AUTH=accounts needs TESTO_API_TOKEN: it signs the session cookies and "
            "stays the admin credential for CI.\n"
        )
        return 2
    from qasight.engine.backends import runner_kind

    if runner_kind() == "container" and not (
        os.environ.get("TESTO_RUNNER_URL", "").strip()
        and os.environ.get("TESTO_RUNNER_TOKEN", "").strip()
    ):
        sys.stderr.write(
            "TESTO_RUNNER=container needs TESTO_RUNNER_URL and TESTO_RUNNER_TOKEN: "
            "the runner service that starts each stage in its own container.\n"
        )
        return 2
    if (
        os.environ.get("TESTO_PROTECT_READS", "0") in ("1", "true", "True")
        and not os.environ.get("TESTO_API_TOKEN", "").strip()
    ):
        logger.warning(
            "TESTO_PROTECT_READS has no effect without TESTO_API_TOKEN; reads stay open."
        )
    # A factory, so the app reads the environment as it is now: ``qasight ui`` sets
    # TESTO_CORS_ORIGINS after ``qasight_api`` (and its module-level ``app``) was imported.
    uvicorn.run("qasight_api.main:create_app", factory=True, host=host, port=port, reload=reload)
    return 0
