"""
Service: Allure results aggregation, metrics JSON export, and archive history listing.
"""

from __future__ import annotations

from pathlib import Path

from qasight.reporting.metrics import (
    RunMetrics,
    list_run_history,
    parse_allure_results_dir,
    write_metrics_json,
)
from qasight.reporting.metrics_extractor import ExtractedMetrics, extract_best


class MetricsService:
    """Read and persist KPI-style metrics derived from Allure ``*-result.json`` files."""

    @staticmethod
    def parse_allure_results_dir(results_dir: Path) -> RunMetrics:
        return parse_allure_results_dir(results_dir)

    @staticmethod
    def write_metrics_json(metrics: RunMetrics, *, out_path: Path) -> Path:
        return write_metrics_json(metrics, out_path=out_path)

    @staticmethod
    def list_run_history(
        *, archive_root: Path, current_results_dir: Path | None = None
    ) -> list[RunMetrics]:
        return list_run_history(archive_root=archive_root, current_results_dir=current_results_dir)

    @staticmethod
    def extract_best(
        *,
        report_dir: Path | None = None,
        results_dir: Path | None = None,
    ) -> ExtractedMetrics | None:
        return extract_best(report_dir=report_dir, results_dir=results_dir)
