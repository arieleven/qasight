"""Tests for service façades."""

from __future__ import annotations

from pathlib import Path

from qasight.services.metrics_service import MetricsService
from qasight.services.report_service import ReportService


def test_report_service_paths(tmp_path: Path) -> None:
    svc = ReportService(artifacts_root=tmp_path)
    p = svc.report_paths()
    assert p.results_dir == tmp_path / "allure-results"


def test_report_service_static_flags() -> None:
    # ``static_reports_ready`` now returns a single boolean since Locust HTML
    # reports were dropped from the framework.
    ready = ReportService.static_reports_ready()
    assert isinstance(ready, bool)


def test_metrics_service_parse_empty_tree(tmp_path: Path) -> None:
    (tmp_path / "allure-results").mkdir(parents=True)
    m = MetricsService.parse_allure_results_dir(tmp_path / "allure-results")
    assert m.total_tests == 0
