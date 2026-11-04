"""Coverage for ``ReportService`` (per-framework entrypoints)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from qasight.services.report_service import ReportService


def test_generate_individual_allure_delegates(tmp_path: Path) -> None:
    svc = ReportService(artifacts_root=tmp_path)
    (tmp_path / "allure-results").mkdir(parents=True, exist_ok=True)
    fake = MagicMock()
    fake.returncode = 0
    fake.stderr = ""
    fake.stdout = "ok"

    def run_ok(cmd: list[str], **_kw: object):
        return fake

    with patch(
        "qasight.reporting.report_generator.publish_allure_index_to_static", lambda **_: None
    ):
        out = svc.generate_individual_allure(frameworks=["pytest"], subprocess_run=run_ok)
    assert "pytest" in out


def test_static_reports_ready_detects_any_framework_index(tmp_path: Path, monkeypatch) -> None:
    # Point STATIC_DIR-derived constants at a temp root by patching the module symbols.
    from qasight.services import report_service as rs

    fake_static = tmp_path / "static"
    fake_reports = fake_static / "allure_reports"
    (fake_reports / "pytest").mkdir(parents=True)
    (fake_reports / "pytest" / "index.html").write_text("ok", encoding="utf-8")

    monkeypatch.setattr(rs, "STATIC_ALLURE_REPORTS_DIR", fake_reports)
    monkeypatch.setattr(
        rs, "STATIC_ALLURE_INDEX", tmp_path / "static" / "allure_report" / "index.html"
    )
    monkeypatch.setattr(rs, "STATIC_ALLURE_HTML", tmp_path / "static" / "allure_report.html")

    assert ReportService.static_reports_ready() is True
