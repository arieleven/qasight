"""Tests for ``qasight.reporting.metrics`` parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.reporting.metrics import RunMetrics, parse_allure_results_dir, write_metrics_json


def test_parse_allure_results_dir_recurse(tmp_path: Path) -> None:
    sub = tmp_path / "pytest"
    sub.mkdir()
    p = sub / "abc-result.json"
    p.write_text(json.dumps({"status": "passed", "start": 1, "stop": 5}), encoding="utf-8")
    m = parse_allure_results_dir(tmp_path)
    assert isinstance(m, RunMetrics)
    assert m.total_tests == 1
    assert m.passed == 1


def test_write_metrics_json_roundtrip(tmp_path: Path) -> None:
    m = RunMetrics(
        timestamp=1,
        total_tests=2,
        passed=1,
        failed=1,
        broken=0,
        skipped=0,
        unknown=0,
        duration_ms=100,
        run_id="rid",
    )
    out = write_metrics_json(m, out_path=tmp_path / "m.json")
    assert out.read_text(encoding="utf-8").count("total_tests") >= 1


def test_parse_allure_results_dir_skips_bad_files_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "ok-result.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    (tmp_path / "torn-result.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "list-result.json").write_text("[]", encoding="utf-8")

    with caplog.at_level("WARNING", logger="qasight.reporting.metrics"):
        m = parse_allure_results_dir(tmp_path)

    assert (m.total_tests, m.failed) == (1, 1)
    assert "torn-result.json" in caplog.text
    assert "list-result.json" in caplog.text
