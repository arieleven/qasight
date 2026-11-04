"""A test listed more than once in one report becomes one result (2026-11-01).

pytest (before 8) wrote a second ``<testcase>`` for an error in teardown, and
``go test -count=N`` reports each run. Counting each listing as its own test
inflated totals and, for a failure followed by a pass, hid the flake.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.reporting.imported_results import fold_attempts
from qasight.reporting.junit_import import import_junit_reports

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _import(tmp_path: Path, name: str, content: str):  # noqa: ANN202
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / name).write_text(content, encoding="utf-8")
    results_dir = tmp_path / "results"
    imported = import_junit_reports(
        target_repo=repo, patterns=(name,), results_dir=results_dir, tool="t"
    )
    results = [json.loads(p.read_text(encoding="utf-8")) for p in results_dir.glob("*-result.json")]
    return imported, {r["fullName"]: r for r in results}


def test_pytest_teardown_error_listing_is_one_broken_test(tmp_path: Path) -> None:
    xml = """<testsuites><testsuite name="pytest" tests="2" errors="1">
      <testcase classname="t" name="test_ok" time="0.1"/>
      <testcase classname="t" name="test_ok" time="0.0">
        <error message="failed on teardown with &quot;RuntimeError: boom&quot;">trace</error>
      </testcase>
    </testsuite></testsuites>"""
    imported, results = _import(tmp_path, "r.xml", xml)
    assert imported.tests == 1
    assert imported.failed == 1
    assert results["t.test_ok"]["status"] == "broken"
    assert "teardown" in results["t.test_ok"]["statusDetails"]["message"]


def test_fail_then_pass_in_one_suite_is_flaky(tmp_path: Path) -> None:
    xml = """<testsuite name="s" tests="3">
      <testcase classname="s" name="a"><failure message="boom">tb</failure></testcase>
      <testcase classname="s" name="a"/>
      <testcase classname="s" name="b"/>
    </testsuite>"""
    imported, results = _import(tmp_path, "r.xml", xml)
    assert imported.tests == 2
    assert imported.failed == 0
    assert imported.flaky == 1
    details = results["s.a"]["statusDetails"]
    assert details["flaky"] is True
    assert details["message"] == "Passed on rerun. boom"
    assert details["trace"] == "tb"


def test_same_name_in_different_suites_stays_separate(tmp_path: Path) -> None:
    xml = """<testsuites>
      <testsuite name="one"><testcase classname="c" name="a"/></testsuite>
      <testsuite name="two"><testcase classname="c" name="a"><failure message="x"/></testcase></testsuite>
    </testsuites>"""
    imported, _ = _import(tmp_path, "r.xml", xml)
    assert imported.tests == 2
    assert imported.failed == 1


def test_suite_claiming_more_tests_than_it_lists_gets_a_note(tmp_path: Path) -> None:
    xml = '<testsuite name="s" tests="5"><testcase classname="s" name="a"/></testsuite>'
    imported, _ = _import(tmp_path, "r.xml", xml)
    assert imported.tests == 1
    assert any("says it has 5 tests but the file lists 1" in e for e in imported.errors)


def test_suite_counts_that_match_get_no_note(tmp_path: Path) -> None:
    xml = """<testsuites tests="9"><testsuite name="s" tests="1">
      <testcase classname="s" name="a"/></testsuite></testsuites>"""
    imported, _ = _import(tmp_path, "r.xml", xml)
    assert imported.errors == ()


def _event(action: str, test: str | None = None, **extra: object) -> str:
    event = {"Action": action, "Package": "example.com/m", **extra}
    if test:
        event["Test"] = test
    return json.dumps(event)


def test_go_count_runs_fold_into_one_result_each(tmp_path: Path) -> None:
    lines = [
        # -count=2: TestFlaky fails then passes, TestLate passes then fails.
        _event("run", "TestFlaky"),
        _event("output", "TestFlaky", Output="    f_test.go:10: first run fails\n"),
        _event("fail", "TestFlaky", Elapsed=0.01),
        _event("run", "TestLate"),
        _event("pass", "TestLate", Elapsed=0.01),
        _event("run", "TestFlaky"),
        _event("pass", "TestFlaky", Elapsed=0.01),
        _event("run", "TestLate"),
        _event("output", "TestLate", Output="    f_test.go:16: fails on second run\n"),
        _event("fail", "TestLate", Elapsed=0.01),
        _event("fail", Elapsed=0.05),
    ]
    imported, results = _import(tmp_path, "go.json", "\n".join(lines) + "\n")
    assert imported.tests == 2  # no "(package failed)" for a package whose tests failed
    assert imported.flaky == 1
    flaky = results["example.com/m.TestFlaky"]
    assert flaky["status"] == "passed"
    assert flaky["statusDetails"]["flaky"] is True
    assert flaky["statusDetails"]["message"] == "Passed on rerun. f_test.go:10: first run fails"
    assert results["example.com/m.TestLate"]["status"] == "failed"


def test_fold_keeps_order_and_single_results_untouched() -> None:
    a = {"uuid": "1", "historyId": "a", "status": "passed", "start": 0, "stop": 1}
    b1 = {"uuid": "2", "historyId": "b", "status": "skipped", "start": 1, "stop": 2}
    b2 = {"uuid": "3", "historyId": "b", "status": "passed", "start": 2, "stop": 5}
    folded = fold_attempts([b1, a, b2])
    assert [r["historyId"] for r in folded] == ["b", "a"]
    assert folded[0]["status"] == "passed"  # skipped ranks below passed
    assert (folded[0]["start"], folded[0]["stop"], folded[0]["uuid"]) == (1, 5, "2")
    assert folded[1] is a
