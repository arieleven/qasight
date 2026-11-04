"""A run that went red must never be recorded as green.

Each case here is a report shape a real runner writes when something failed
outside any single test (a hook, a build, a file that doesn't load, a crashed
host, a file cut off mid-write). The shapes were taken from Go 1.24, Jest 29,
Vitest 3 and Playwright 1.48 output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.engine.executor import run_stage
from qasight.reporting.junit_import import import_junit_reports
from qasight.services.cycle_run import junit_ingest_plan

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _import(tmp_path: Path, name: str, content: str) -> tuple[object, dict[str, dict]]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / name).write_text(content, encoding="utf-8")
    results_dir = tmp_path / "results"
    imported = import_junit_reports(
        target_repo=repo, patterns=(name,), results_dir=results_dir, tool="t"
    )
    results = {}
    for path in results_dir.glob("*-result.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        results[data["fullName"]] = data
    return imported, results


def _lines(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


# --- go test -json -------------------------------------------------------------------


def test_go_124_build_failure_is_a_broken_package(tmp_path: Path) -> None:
    text = _lines(
        {
            "ImportPath": "example.com/m [example.com/m.test]",
            "Action": "build-output",
            "Output": "# example.com/m [example.com/m.test]\n",
        },
        {
            "ImportPath": "example.com/m [example.com/m.test]",
            "Action": "build-output",
            "Output": "./a_test.go:4:33: undefined: undefinedThing\n",
        },
        {"ImportPath": "example.com/m [example.com/m.test]", "Action": "build-fail"},
        {"Action": "start", "Package": "example.com/m"},
        {
            "Action": "output",
            "Package": "example.com/m",
            "Output": "FAIL\texample.com/m [build failed]\n",
        },
        {
            "Action": "fail",
            "Package": "example.com/m",
            "Elapsed": 0,
            "FailedBuild": "example.com/m [example.com/m.test]",
        },
    )
    imported, results = _import(tmp_path, "go.json", text)

    assert imported.errors == ()
    package = results["example.com/m.(package failed)"]
    assert package["status"] == "broken"
    assert package["statusDetails"]["message"] == "./a_test.go:4:33: undefined: undefinedThing"
    assert "undefined: undefinedThing" in package["statusDetails"]["trace"]


def test_go_test_that_never_finished_is_broken(tmp_path: Path) -> None:
    text = _lines(
        {"Action": "run", "Package": "p", "Test": "TestOK"},
        {"Action": "pass", "Package": "p", "Test": "TestOK", "Elapsed": 0},
        {"Action": "run", "Package": "p", "Test": "TestSlow"},
        {
            "Action": "output",
            "Package": "p",
            "Test": "TestSlow",
            "Output": "panic: test timed out after 1s\n",
        },
        {"Action": "fail", "Package": "p", "Elapsed": 1.0},
    )
    _, results = _import(tmp_path, "go.json", text)

    # The hung test is named, and the package isn't counted a second time.
    assert {k: v["status"] for k, v in results.items()} == {
        "p.TestOK": "passed",
        "p.TestSlow": "broken",
    }
    assert "timed out" in results["p.TestSlow"]["statusDetails"]["message"]


# --- Jest / Vitest -------------------------------------------------------------------


def test_jest_after_all_failure_is_not_a_pass(tmp_path: Path) -> None:
    report = {
        "success": False,
        "testResults": [
            {
                "name": "/ci/app/hook.test.js",
                "status": "failed",
                "message": "  ● Test suite failed to run\n\n    afterAll boom\n",
                "assertionResults": [{"title": "passes", "status": "passed"}],
            }
        ],
    }
    imported, results = _import(tmp_path, "jest.json", json.dumps(report))

    assert imported.failed == 1
    assert results["hook.test.js › passes"]["status"] == "passed"
    hook = results["hook.test.js › (test file failed)"]
    assert hook["status"] == "broken"
    assert hook["statusDetails"]["message"] == "Test suite failed to run"
    assert "afterAll boom" in hook["statusDetails"]["trace"]


def test_vitest_before_all_failure_is_not_skipped_into_green(tmp_path: Path) -> None:
    report = {
        "success": False,
        "testResults": [
            {
                "name": "/ci/app/before.test.ts",
                "status": "failed",
                "message": "beforeAll boom",
                "assertionResults": [{"title": "never", "status": "skipped", "meta": {}}],
            }
        ],
    }
    imported, results = _import(tmp_path, "vitest.json", json.dumps(report))

    assert imported.failed == 1
    assert results["before.test.ts › (test file failed)"]["statusDetails"]["message"] == (
        "beforeAll boom"
    )


def test_jest_file_with_a_failing_test_gets_no_extra_result(tmp_path: Path) -> None:
    report = {
        "testResults": [
            {
                "name": "/ci/app/a.test.js",
                "status": "failed",
                "message": "  ● fails\n\n    boom",
                "assertionResults": [{"title": "fails", "status": "failed"}],
            }
        ]
    }
    _, results = _import(tmp_path, "jest.json", json.dumps(report))
    assert list(results) == ["a.test.js › fails"]


# --- Playwright ----------------------------------------------------------------------


def test_playwright_load_error_is_a_broken_result(tmp_path: Path) -> None:
    report = {
        "suites": [],
        "errors": [
            {
                "message": "SyntaxError: /ci/app/tests/bad.spec.ts: Unexpected token (1:64)",
                "stack": "SyntaxError: /ci/app/tests/bad.spec.ts: Unexpected token (1:64)",
                "location": {"file": "/ci/app/tests/bad.spec.ts", "line": 1, "column": 64},
            }
        ],
        "stats": {"startTime": "2026-10-31T10:22:30.278Z", "expected": 0, "unexpected": 0},
    }
    imported, results = _import(tmp_path, "pw.json", json.dumps(report))

    assert imported.tests == 1 and imported.failed == 1
    error = results["bad.spec.ts › (failed to load)"]
    assert error["status"] == "broken"
    assert error["statusDetails"]["message"].startswith("SyntaxError:")
    # Still noted in the log, as before.
    assert any("report error: SyntaxError" in e for e in imported.errors)


# --- TRX -----------------------------------------------------------------------------


def test_trx_host_crash_is_a_broken_result(tmp_path: Path) -> None:
    trx = """<?xml version="1.0" encoding="utf-8"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <ResultSummary outcome="Failed">
    <Counters total="0" executed="0" passed="0" failed="0" />
    <RunInfos>
      <RunInfo computerName="ci" outcome="Warning"><Text>No test is available.</Text></RunInfo>
      <RunInfo computerName="ci" outcome="Error">
        <Text>The active test run was aborted. Reason: Test host process crashed</Text>
      </RunInfo>
    </RunInfos>
  </ResultSummary>
</TestRun>"""
    imported, results = _import(tmp_path, "run.trx", trx)

    assert imported.failed == 1
    error = results["(test run error)"]
    assert "Test host process crashed" in error["statusDetails"]["message"]
    assert "No test is available" not in error["statusDetails"]["trace"]


# --- JUnit ---------------------------------------------------------------------------


def test_junit_suite_that_claims_errors_without_a_failing_case(tmp_path: Path) -> None:
    xml = (
        '<testsuites><testsuite name="cart.test.js" errors="1" failures="0" tests="0">'
        "<system-err>Error: Cannot find module './cart'</system-err>"
        "</testsuite></testsuites>"
    )
    imported, results = _import(tmp_path, "junit.xml", xml)

    assert imported.failed == 1
    error = results["cart.test.js.(suite failed)"]
    assert error["statusDetails"]["message"] == "Error: Cannot find module './cart'"


def test_junit_suite_whose_failures_are_cases_gets_no_extra_result(tmp_path: Path) -> None:
    xml = (
        '<testsuite name="s" failures="1" errors="0" tests="2">'
        '<testcase name="a"/><testcase name="b"><failure message="m"/></testcase></testsuite>'
    )
    imported, results = _import(tmp_path, "junit.xml", xml)
    assert imported.tests == 2


# --- qasight ingest: the stage's returncode --------------------------------------------


def _ingest(tmp_path: Path, name: str, content: str):  # noqa: ANN202
    (tmp_path / name).write_text(content, encoding="utf-8")
    stage = junit_ingest_plan(cycle="web", reports=[name], target_repo=tmp_path).stages[0]
    return run_stage(stage, plan_name="web", artifacts_root=tmp_path / "out")


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("cut.xml", '<testsuite name="s"><testcase name="a"/><testcase name="b"><failure>bo'),
        ("empty.xml", ""),
        ("cut.json", '{"testResults": [{"name": "a.test.js", "assertionResults": [{"tit'),
    ],
)
def test_ingesting_only_unreadable_files_fails(tmp_path: Path, name: str, content: str) -> None:
    result = _ingest(tmp_path, name, content)
    assert result.returncode == 1
    assert result.error is not None and "could not be read" in result.error


def test_one_unreadable_file_next_to_a_good_one_keeps_the_good_results(tmp_path: Path) -> None:
    (tmp_path / "good.xml").write_text(
        '<testsuite name="s"><testcase name="a"/></testsuite>', encoding="utf-8"
    )
    (tmp_path / "junk.xml").write_text("<testsuite", encoding="utf-8")
    stage = junit_ingest_plan(cycle="web", reports=["*.xml"], target_repo=tmp_path).stages[0]
    result = run_stage(stage, plan_name="web", artifacts_root=tmp_path / "out")
    assert result.returncode == 0
    assert "skipped junk.xml" in result.log_path.read_text()  # type: ignore[union-attr]
