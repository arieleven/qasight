"""Odd report files: one bad file or number never loses the rest, nothing is dropped silently."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.engine.executor import run_stage
from qasight.engine.result import StageResult
from qasight.reporting.junit_import import import_junit_reports
from qasight.services.cycle_run import junit_ingest_plan

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

GOOD = '<testsuite name="s"><testcase name="good"/></testsuite>'


def _ingest(tmp_path: Path, *patterns: str) -> StageResult:
    stage = junit_ingest_plan(cycle="web", reports=list(patterns), target_repo=tmp_path).stages[0]
    return run_stage(stage, plan_name="web", artifacts_root=tmp_path / "out")


def _import(tmp_path: Path, *patterns: str):  # noqa: ANN202
    return import_junit_reports(
        target_repo=tmp_path, patterns=patterns, results_dir=tmp_path / "res", tool="t"
    )


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("inf.xml", '<testsuite name="s"><testcase name="a" time="inf"/></testsuite>'),
        (
            "jest.json",
            '{"testResults":[{"name":"a","assertionResults":'
            '[{"title":"t","status":"passed","duration":Infinity}]}]}',
        ),
        ("go.json", '{"Action":"pass","Package":"p","Test":"T","Elapsed":1e999}\n'),
    ],
)
def test_infinite_duration_records_the_test(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / name).write_text(body, encoding="utf-8")
    imported = _import(tmp_path, name)
    assert imported.tests == 1
    assert imported.errors == ()


def test_a_structurally_odd_file_is_skipped_and_the_others_kept(tmp_path: Path) -> None:
    (tmp_path / "pw.json").write_text('{"suites":[1]}', encoding="utf-8")
    (tmp_path / "good.xml").write_text(GOOD, encoding="utf-8")
    imported = _import(tmp_path, "*")
    assert imported.tests == 1


def test_nested_testsuites_are_all_counted(tmp_path: Path) -> None:
    (tmp_path / "n.xml").write_text(
        '<testsuite name="outer"><testcase name="top"/><testsuite name="inner">'
        '<testcase name="x"/><testcase name="y"><failure/></testcase></testsuite></testsuite>',
        encoding="utf-8",
    )
    imported = _import(tmp_path, "n.xml")
    assert (imported.tests, imported.failed) == (3, 1)


def test_testcases_directly_under_testsuites_are_counted(tmp_path: Path) -> None:
    (tmp_path / "b.xml").write_text(
        '<testsuites><testcase name="z"/><testsuite name="s"><testcase name="w"/></testsuite>'
        "</testsuites>",
        encoding="utf-8",
    )
    assert _import(tmp_path, "b.xml").tests == 2


def test_utf16_junit_is_read(tmp_path: Path) -> None:
    (tmp_path / "u.xml").write_text(
        '<?xml version="1.0" encoding="UTF-16"?>' + GOOD, encoding="utf-16"
    )
    assert _import(tmp_path, "u.xml").tests == 1


def test_playwright_report_with_bom_is_read(tmp_path: Path) -> None:
    report = {
        "config": {"rootDir": str(tmp_path)},
        "suites": [
            {
                "title": "a.spec.ts",
                "file": "a.spec.ts",
                "specs": [
                    {
                        "title": "works",
                        "file": "a.spec.ts",
                        "tests": [
                            {
                                "projectName": "chromium",
                                "status": "expected",
                                "results": [{"status": "passed", "duration": 5, "retry": 0}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    (tmp_path / "pw.json").write_text(json.dumps(report), encoding="utf-8-sig")
    imported = _import(tmp_path, "pw.json")
    assert imported.errors == ()
    assert imported.tests == 1


@pytest.mark.parametrize("name", ["report[1].xml", "test-attachments.xml", "environment.xml"])
def test_report_names_that_look_special_are_still_read(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text(GOOD, encoding="utf-8")
    assert _import(tmp_path, name).tests == 1


def test_allure_attachments_and_environment_are_still_skipped(tmp_path: Path) -> None:
    (tmp_path / "abc-attachment.txt").write_text("log", encoding="utf-8")
    (tmp_path / "environment.xml").write_text("<environment/>", encoding="utf-8")
    (tmp_path / "abc-result.json").write_text(
        json.dumps({"uuid": "abc", "name": "t", "status": "passed"}), encoding="utf-8"
    )
    imported = _import(tmp_path, "*")
    assert imported.tests == 1
    assert imported.errors == ()


def test_ingest_fails_when_no_matched_file_could_be_read(tmp_path: Path) -> None:
    (tmp_path / "empty.xml").write_text("", encoding="utf-8")
    result = _ingest(tmp_path, "empty.xml")
    assert result.returncode == 1
    assert result.error is not None and "could not be read" in result.error


def test_ingest_of_an_empty_but_valid_report_still_passes(tmp_path: Path) -> None:
    (tmp_path / "none.xml").write_text('<testsuite name="s"/>', encoding="utf-8")
    result = _ingest(tmp_path, "none.xml")
    assert result.returncode == 0
    assert result.error is None
