"""ExtentReports-style local HTML dashboard (Jinja2, dark theme)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import TemplateError

from qasight.reporting.allure_results import parse_collected_results
from qasight.reporting.collector import CollectedResults
from qasight.reporting.exporter import write_json_summary
from qasight.reporting.reporters.base import BaseReporter, ReportContext, ReporterResult
from qasight.reporting.reporters.extent_builder import render_dashboard

if TYPE_CHECKING:
    from rich.console import Console

logger = logging.getLogger(__name__)


class ExtentReporter(BaseReporter):
    @property
    def reporter_type(self) -> str:
        return "extent"

    def publish(
        self,
        *,
        results: CollectedResults,
        context: ReportContext,
        console: Console | None = None,
    ) -> ReporterResult:
        if not results.stages:
            return ReporterResult(ok=False, message="no results to export for Extent report.")

        default_dir = (
            context.run_report_root / "extent_report"
            if context.run_report_root is not None
            else context.artifacts_root / "reports" / "extent"
        )
        output_dir = (
            Path(self._options.get("output_dir") or str(default_dir)).expanduser().resolve()
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        aggregate = parse_collected_results(results)
        summary_path = output_dir / "summary.json"
        write_json_summary(results=results, out=summary_path)

        try:
            index_path = render_dashboard(aggregate, context=context, output_dir=output_dir)
        except (OSError, TemplateError) as exc:
            logger.warning("Extent dashboard render failed in %s", output_dir, exc_info=True)
            return ReporterResult(ok=False, message=f"Extent dashboard render failed: {exc}")

        msg = f"Extent report at {index_path}"
        if console is not None:
            console.print(f"[ok]{msg}[/]")
        return ReporterResult(ok=True, message=msg, artifacts=(index_path, summary_path))
