"""``POST /api/v1/ingest``: record uploaded test result files (JUnit XML and the other formats junit_import reads) as a run (``qasight ingest --server``)."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile

from qasight.config.errors import ConfigError, ConfigValidationError
from qasight.config.loader import load_config_or_defaults
from qasight.services.junit_ingest import (
    MAX_FILE_BYTES,
    clean_run_metadata,
    ingest_uploaded_reports,
)
from qasight_api.models import IngestResponse
from qasight_api.ui import ui_dir

router = APIRouter(prefix="/api/v1", tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse, status_code=201)
def ingest_reports(
    request: Request,
    files: list[UploadFile],
    cycle: str = Form(...),
    stage: str = Form("junit"),
    tier: str = Form("unit"),
    ci_provider: str | None = Form(None),
    ci_pipeline_id: str | None = Form(None),
    ci_job_id: str | None = Form(None),
    ci_commit_sha: str | None = Form(None),
    ci_ref_name: str | None = Form(None),
    ci_base_ref: str | None = Form(None),
    repository: str | None = Form(None),
    environment: str | None = Form(
        None, max_length=500, description='What the run tested on: "browser=firefox, os=linux".'
    ),
    release: str | None = Form(None, max_length=60, description='The release it tested: "2.3".'),
    coverage_covered: str | None = Form(None),
    coverage_total: str | None = Form(None),
) -> IngestResponse:
    """Record JUnit XML files as one run of *cycle*. Nothing in the upload is executed.

    ``exit_code`` follows ``qasight run``: 0 when every test passed or was skipped,
    1 when one failed. Needs ``Authorization: Bearer <TESTO_API_TOKEN>`` when the
    server has a token set, like every other write.
    """
    try:
        cfg = load_config_or_defaults()
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=f"config error: {exc}") from exc

    reports: list[tuple[str, bytes]] = []
    for upload in files:
        # Read one byte past the limit so an oversized file is rejected, not truncated.
        content = upload.file.read(MAX_FILE_BYTES + 1)
        reports.append((upload.filename or "report.xml", content))

    metadata = clean_run_metadata(
        {
            "ci_provider": ci_provider,
            "ci_pipeline_id": ci_pipeline_id,
            "ci_job_id": ci_job_id,
            "ci_commit_sha": ci_commit_sha,
            "ci_ref_name": ci_ref_name,
            "ci_base_ref": ci_base_ref,
            "repository": repository,
            "environment": environment,
            "release": release,
            "coverage_covered": coverage_covered,
            "coverage_total": coverage_total,
        }
    )
    try:
        outcome = ingest_uploaded_reports(
            cfg=cfg,
            cycle=cycle.strip(),
            reports=reports,
            stage=stage.strip(),
            tier=tier.strip().lower(),
            run_metadata=metadata,
        )
    except ConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    base = str(request.base_url).rstrip("/")
    return IngestResponse(
        run_id=outcome.run_id,
        cycle=outcome.cycle,
        exit_code=outcome.exit_code,
        tests=outcome.tests,
        passed=outcome.passed,
        failed=outcome.failed,
        skipped=outcome.skipped,
        # A page link only when this process serves the UI (the team server does).
        run_url=f"{base}/runs/{outcome.run_id}" if outcome.run_id and ui_dir() else None,
    )
