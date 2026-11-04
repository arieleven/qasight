"""Coverage for small defaults and placeholder runners."""

from __future__ import annotations

from pathlib import Path

from qasight.reporting.report_generator import default_report_paths


def test_default_report_paths_points_to_static(tmp_path: Path) -> None:
    p = default_report_paths(artifacts_root=tmp_path)
    assert p.results_dir == tmp_path / "allure-results"
    # output is always the static allure dir (not under artifacts)
    assert "static" in str(p.report_dir)
