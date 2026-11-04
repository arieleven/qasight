"""ReportPortal REST reporter — launch lifecycle and Allure streaming."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

from qasight.reporting.allure_results import parse_collected_results
from qasight.reporting.collector import CollectedResults
from qasight.reporting.exporter import write_json_summary
from qasight.reporting.reporters.base import BaseReporter, ReportContext, ReporterResult
from qasight.reporting.reporters.reportportal_client import (
    ReportPortalClient,
    ReportPortalError,
)

if TYPE_CHECKING:
    from rich.console import Console

logger = logging.getLogger(__name__)


class ReportPortalReporter(BaseReporter):
    @property
    def reporter_type(self) -> str:
        return "reportportal"

    def publish(
        self,
        *,
        results: CollectedResults,
        context: ReportContext,
        console: Console | None = None,
    ) -> ReporterResult:
        endpoint = (self._options.get("endpoint") or "").strip().rstrip("/")
        project = (self._options.get("project") or "").strip()
        token = (self._options.get("token") or "").strip()
        api_version = (self._options.get("api_version") or "").strip() or None
        if api_version and api_version.lower() == "auto":
            api_version = None
        launch_name = (
            (self._options.get("launch_name") or "").strip()
            or context.plan_name
            or context.run_id
            or "testo-run"
        )

        if not endpoint or not project or not token:
            return ReporterResult(
                ok=False,
                message="reportportal reporter requires endpoint, project, and token options.",
            )

        if not results.stages:
            return ReporterResult(ok=False, message="no results to upload to ReportPortal.")

        out_dir = context.artifacts_root / "reports" / "reportportal"
        out_dir.mkdir(parents=True, exist_ok=True)
        write_json_summary(results=results, out=out_dir / "summary.json")

        aggregate = parse_collected_results(results)
        attributes = [
            {"key": "layout", "value": context.layout},
            {"key": "plan", "value": context.plan_name or ""},
        ]

        try:
            client = ReportPortalClient(
                endpoint,
                project,
                token,
                api_version=api_version,
            )
            client.validate()
            launch_uuid = client.report_aggregate(
                aggregate,
                launch_name=launch_name,
                attributes=attributes,
            )
            dashboard = client.dashboard_url(launch_uuid)
        except ReportPortalError as exc:
            logger.warning("ReportPortal rejected the upload: %s", exc)
            return ReporterResult(ok=False, message=str(exc))
        except requests.RequestException as exc:
            logger.warning("ReportPortal upload failed", exc_info=True)
            return ReporterResult(ok=False, message=f"ReportPortal upload failed: {exc}")

        msg = f"ReportPortal launch {launch_uuid} — {dashboard}"
        if console is not None:
            console.print(f"[ok]{msg}[/]")
        return ReporterResult(
            ok=True,
            message=msg,
            artifacts=(out_dir / "summary.json",),
        )
