"""Coverage for static mirroring helpers in ``qasight.reporting.report_generator``."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.reporting import report_generator as rg


def test_publish_allure_index_to_static_chmod_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Make report_dir == STATIC_ALLURE_REPORT_DIR
    static_dir = tmp_path / "static"
    report_dir = static_dir / "allure_report"
    report_dir.mkdir(parents=True)
    (report_dir / "index.html").write_text("<html/>", encoding="utf-8")

    monkeypatch.setattr(rg, "STATIC_DIR", static_dir)
    monkeypatch.setattr(rg, "STATIC_ALLURE_REPORT_DIR", report_dir)
    monkeypatch.setattr(rg, "STATIC_ALLURE_INDEX", report_dir / "index.html")

    out = rg.publish_allure_index_to_static(report_dir=report_dir)
    assert out is not None
