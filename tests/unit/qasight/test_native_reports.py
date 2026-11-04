"""Tests for :mod:`qasight.reporting.native_reports`."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from qasight.reporting.native_reports import (
    ensure_behavex_report_html,
    list_native_rows,
    load_stage_equipment,
    native_row_for_stage,
    resolve_cycle_dir,
)


def test_resolve_cycle_dir_explicit(tmp_path: Path) -> None:
    c = tmp_path / "my-cycle"
    c.mkdir()
    (c / "events.ndjson").write_text("{}", encoding="utf-8")
    assert resolve_cycle_dir(artifacts_root=tmp_path, cycle="my-cycle") == c.resolve()


def test_resolve_cycle_dir_latest(tmp_path: Path) -> None:
    old = tmp_path / "old"
    old.mkdir()
    (old / "events.ndjson").write_text("{}", encoding="utf-8")
    new = tmp_path / "new"
    new.mkdir()
    (new / "events.ndjson").write_text("{}", encoding="utf-8")
    newer = time.time() + 10.0
    os.utime(new / "events.ndjson", (newer, newer))
    got = resolve_cycle_dir(artifacts_root=tmp_path, cycle=None)
    assert got is not None
    assert got.name == "new"


def test_load_stage_equipment(tmp_path: Path) -> None:
    plan = tmp_path / "plan_result.json"
    plan.write_text(
        json.dumps(
            {
                "stages": [
                    {"name": "a", "framework": "behavex"},
                    {"name": "b", "framework": "pytest"},
                ]
            }
        ),
        encoding="utf-8",
    )
    m = load_stage_equipment(tmp_path)
    assert m == {"a": "behavex", "b": "pytest"}


def test_behavex_native_row_with_report_html(tmp_path: Path) -> None:
    stage = tmp_path / "flow-tests"
    br = stage / "behave_reports"
    br.mkdir(parents=True)
    (br / "report.html").write_text("<html/>", encoding="utf-8")
    row = native_row_for_stage(stage, "behavex")
    assert row.open_path == (br / "report.html").resolve()
    assert row.open_kind == "html"


def test_behavex_html_generation_failure_is_logged_or_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    report_html = pytest.importorskip("behavex.outputs.report_html", reason="needs qasight[bdd]")

    def broken_generate(_payload: object) -> None:
        raise RuntimeError("template missing")

    monkeypatch.setattr(report_html, "generate_report", broken_generate)
    stage = tmp_path / "flow-tests"
    (stage / "behave_reports").mkdir(parents=True)
    (stage / "behave_reports" / "report.json").write_text("{}", encoding="utf-8")

    with caplog.at_level("WARNING", logger="qasight.reporting.native_reports"):
        assert ensure_behavex_report_html(stage) is None
    assert "BehaveX HTML report generation failed" in caplog.text

    with pytest.raises(RuntimeError, match="template missing"):
        ensure_behavex_report_html(stage, raise_errors=True)

    # A failing first root doesn't stop the second one from being used.
    fallback = stage / "allure-results" / "behave_reports"
    fallback.mkdir(parents=True)
    (fallback / "report.html").write_text("<html/>", encoding="utf-8")
    assert ensure_behavex_report_html(stage, raise_errors=True) == fallback / "report.html"


def test_behavex_assets_only(tmp_path: Path) -> None:
    stage = tmp_path / "flow-tests"
    br = stage / "behave_reports" / "behavex_images"
    br.mkdir(parents=True)
    row = native_row_for_stage(stage, "behavex")
    assert row.open_path is None
    assert "assets" in row.notes


def test_list_native_rows_integration(tmp_path: Path) -> None:
    cycle = tmp_path / "behavex-flow-tests"
    cycle.mkdir()
    (cycle / "plan_result.json").write_text(
        json.dumps({"stages": [{"name": "flow-tests", "framework": "behavex"}]}),
        encoding="utf-8",
    )
    stage = cycle / "flow-tests"
    br = stage / "behave_reports"
    br.mkdir(parents=True)
    (stage / "run.log").write_text("ok\n", encoding="utf-8")
    (br / "report.html").write_text("<html/>", encoding="utf-8")

    rows = list_native_rows(cycle_dir=cycle)
    assert len(rows) == 1
    assert rows[0].routine == "flow-tests"
    assert rows[0].open_path is not None


def test_pytest_junit_shallow(tmp_path: Path) -> None:
    stage = tmp_path / "api"
    stage.mkdir()
    (stage / "run.log").write_text("", encoding="utf-8")
    (stage / "junit.xml").write_text("<testsuites/>", encoding="utf-8")
    row = native_row_for_stage(stage, "pytest")
    assert row.open_path is not None
    assert row.open_kind == "xml"
