"""The manager report: ``GET /api/v1/report`` (the UI's ``/report`` page) and its file downloads."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from testo_api.models import ManagerReportResponse
from testo_core.services.manager_report import build_report

router = APIRouter(prefix="/api/v1", tags=["report"])

Period = Literal["day", "week", "month", "quarter"]
Format = Literal["pdf", "png", "html", "pptx", "xlsx", "csv", "json"]


@router.get("/report", response_model=ManagerReportResponse)
def get_report(
    period: Period = "week",
    cycle: list[str] | None = Query(
        None, description="Cycles to include; repeat. All when left out."
    ),
    title: str | None = Query(None, max_length=120),
) -> ManagerReportResponse:
    """How testing went over the last ``period``, against the period before, for every cycle or ``cycle``."""
    report = build_report(period=period, cycles=cycle, title=title or None)
    return ManagerReportResponse.model_validate(report.to_dict())


@router.get(
    "/report/export",
    response_class=Response,
    responses={
        200: {"description": "The report file, as an attachment."},
        501: {
            "description": "This server can't make that format (the message says what to install)."
        },
    },
)
def export_report(
    format: Format = "pdf",  # noqa: A002  (the query parameter's public name)
    period: Period = "week",
    cycle: list[str] | None = Query(None),
    title: str | None = Query(None, max_length=120),
    brand: str = Query("Testosterone", max_length=60),
) -> Response:
    """The report as a file: the same as ``testo export FORMAT`` on this server's history.

    PDF and PNG need the ``export`` extra and Chromium on the server; PowerPoint and
    Excel need the ``export`` extra; HTML needs the built UI (bundled in released wheels).
    """
    from testo_core.services.report_export import ExportUnavailable, file_name, formats

    report = build_report(period=period, cycles=cycle, title=title or None)
    spec = formats()[format]
    try:
        data = spec.render(report, brand)
    except ExportUnavailable as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    name = file_name(report, spec.extension)
    return Response(
        content=data,
        media_type=spec.media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
