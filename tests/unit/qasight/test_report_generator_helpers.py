"""More ``report_generator`` helpers (no Allure binary)."""

from __future__ import annotations

from pathlib import Path

from qasight.reporting.report_generator import compute_system_health_pct, make_report_zip


def test_compute_system_health_pct_none_when_empty(tmp_path: Path) -> None:
    assert compute_system_health_pct(tmp_path) is None


def test_compute_system_health_pct_value(tmp_path: Path) -> None:
    (tmp_path / "x-result.json").write_text(
        '{"status":"passed","start":0,"stop":1}',
        encoding="utf-8",
    )
    pct = compute_system_health_pct(tmp_path)
    assert pct == 100.0


def test_make_report_zip(tmp_path: Path) -> None:
    src = tmp_path / "rep"
    src.mkdir()
    (src / "index.html").write_text("<html/>", encoding="utf-8")
    zp = make_report_zip(report_dir=src, out_dir=tmp_path, base_name="z")
    assert zp.suffix == ".zip" and zp.is_file()
