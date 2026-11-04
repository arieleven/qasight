"""Runners' own result formats, recognised by content: Jest/Vitest, TRX, Go, Robot, RSpec, k6, XCTest, Allure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.reporting.junit_import import import_junit_reports

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

JEST = {
    "numTotalTests": 5,
    "startTime": 1_760_000_000_000,
    "success": False,
    "testResults": [
        {
            "name": "/ci/work/app/src/cart.test.js",
            "status": "failed",
            "startTime": 1_760_000_000_100,
            "message": "",
            "assertionResults": [
                {
                    "ancestorTitles": ["cart", "totals"],
                    "title": "adds tax",
                    "fullName": "cart totals adds tax",
                    "status": "passed",
                    "duration": 12,
                    "failureMessages": [],
                },
                {
                    "ancestorTitles": ["cart"],
                    "title": "rejects negative",
                    "status": "failed",
                    "duration": 5,
                    "failureMessages": [
                        "Error: expect(received).toBe(expected)\n    at cart.test.js:9"
                    ],
                },
                {"ancestorTitles": [], "title": "later", "status": "todo", "failureMessages": []},
                {
                    "ancestorTitles": [],
                    "title": "retried",
                    "status": "passed",
                    "duration": 3,
                    "failureMessages": [],
                    "retryReasons": ["Error: timeout"],
                },
            ],
        },
        {
            "name": "/ci/work/app/src/broken.test.js",
            "status": "failed",
            "message": "SyntaxError: Unexpected token",
            "assertionResults": [],
        },
    ],
}

TRX = """<?xml version="1.0" encoding="utf-8"?>
<TestRun id="1" name="run" xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Times start="2026-10-26T10:00:00.0000000+00:00" />
  <Results>
    <UnitTestResult testId="a" testName="Shop.Tests.CartTests.AddsTax" outcome="Passed" duration="00:00:00.0120000" />
    <UnitTestResult testId="b" testName="Shop.Tests.CartTests.RejectsNegative" outcome="Failed" duration="00:00:01.5000000">
      <Output><ErrorInfo><Message>Assert.Equal() Failure</Message><StackTrace>at CartTests.cs:line 9</StackTrace></ErrorInfo></Output>
    </UnitTestResult>
    <UnitTestResult testId="c" testName="Shop.Tests.CartTests.Later" outcome="NotExecuted" duration="00:00:00" />
    <UnitTestResult testId="d" testName="Rows" outcome="Failed">
      <InnerResults>
        <UnitTestResult testId="d" testName="Rows(1)" outcome="Passed" duration="00:00:00.0010000" />
        <UnitTestResult testId="d" testName="Rows(2)" outcome="Timeout" duration="00:00:00.0010000" />
      </InnerResults>
    </UnitTestResult>
  </Results>
  <TestDefinitions>
    <UnitTest id="a" name="AddsTax"><TestMethod className="Shop.Tests.CartTests" name="AddsTax" /></UnitTest>
    <UnitTest id="b" name="RejectsNegative"><TestMethod className="Shop.Tests.CartTests" name="RejectsNegative" /></UnitTest>
    <UnitTest id="c" name="Later"><TestMethod className="Shop.Tests.CartTests" name="Later" /></UnitTest>
    <UnitTest id="d" name="Rows"><TestMethod className="Shop.Tests.RowTests" name="Rows" /></UnitTest>
  </TestDefinitions>
</TestRun>
"""


def _go_lines() -> str:
    events = [
        {"Action": "start", "Package": "example.com/shop"},
        {"Action": "run", "Package": "example.com/shop", "Test": "TestCart"},
        {"Action": "run", "Package": "example.com/shop", "Test": "TestCart/tax"},
        {"Action": "pass", "Package": "example.com/shop", "Test": "TestCart/tax", "Elapsed": 0.01},
        {"Action": "run", "Package": "example.com/shop", "Test": "TestCart/negative"},
        {
            "Action": "output",
            "Package": "example.com/shop",
            "Test": "TestCart/negative",
            "Output": "=== RUN   TestCart/negative\n",
        },
        {
            "Action": "output",
            "Package": "example.com/shop",
            "Test": "TestCart/negative",
            "Output": "    cart_test.go:21: want error, got nil\n",
        },
        {
            "Action": "fail",
            "Package": "example.com/shop",
            "Test": "TestCart/negative",
            "Elapsed": 0.02,
        },
        {"Action": "fail", "Package": "example.com/shop", "Test": "TestCart", "Elapsed": 0.03},
        {"Action": "skip", "Package": "example.com/shop", "Test": "TestLater", "Elapsed": 0},
        {"Action": "fail", "Package": "example.com/shop", "Elapsed": 0.5},
        {
            "Action": "output",
            "Package": "example.com/broken",
            "Output": "# example.com/broken\nbroken.go:3: undefined: x\n",
        },
        {"Action": "fail", "Package": "example.com/broken", "Elapsed": 0},
    ]
    return "go: downloading something\n" + "\n".join(json.dumps(e) for e in events) + "\n"


ROBOT = """<?xml version="1.0" encoding="UTF-8"?>
<robot generator="Robot 7.1" generated="2026-10-26T10:00:00.000000">
  <suite id="s1" name="Tests">
    <suite id="s1-s1" name="Login">
      <test id="s1-s1-t1" name="Valid Login">
        <tag>smoke</tag>
        <status status="PASS" start="2026-10-26T10:00:01.000000" elapsed="1.250"/>
      </test>
      <test id="s1-s1-t2" name="Wrong Password">
        <status status="FAIL" start="2026-10-26T10:00:03.000000" elapsed="0.5">Page should have contained 'Error'</status>
      </test>
      <test id="s1-s1-t3" name="Later">
        <status status="SKIP" starttime="20261026 10:00:04.000" endtime="20261026 10:00:04.100">Not ready</status>
      </test>
    </suite>
  </suite>
</robot>
"""


def _import(
    tmp_path: Path, files: dict[str, str | bytes], pattern: str = "out/*", tool: str = "junit"
):
    repo = tmp_path / "repo"
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    results_dir = tmp_path / "results"
    imported = import_junit_reports(
        target_repo=repo, patterns=(pattern,), results_dir=results_dir, tool=tool
    )
    results = {}
    for path in results_dir.glob("*-result.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        results[data["fullName"]] = data
    return imported, results, results_dir


def _label(result: dict, name: str) -> str | None:
    return next((lab["value"] for lab in result["labels"] if lab["name"] == name), None)


def test_jest_json(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/jest.json": json.dumps(JEST)})

    assert imported.errors == ()
    assert (imported.tests, imported.failed) == (5, 2)
    assert {k: v["status"] for k, v in results.items()} == {
        "cart.test.js › cart › totals › adds tax": "passed",
        "cart.test.js › cart › rejects negative": "failed",
        "cart.test.js › later": "skipped",
        "cart.test.js › retried": "passed",
        "broken.test.js › (test file failed to run)": "broken",
    }
    failed = results["cart.test.js › cart › rejects negative"]
    assert failed["statusDetails"]["message"] == "Error: expect(received).toBe(expected)"
    assert _label(failed, "framework") == "jest"
    assert _label(failed, "subSuite") == "cart"
    assert results["cart.test.js › retried"]["statusDetails"]["flaky"] is True
    broken = results["broken.test.js › (test file failed to run)"]
    assert broken["statusDetails"]["message"] == "SyntaxError: Unexpected token"


def test_jest_paths_inside_the_repo_are_relative_to_it(tmp_path: Path) -> None:
    repo = (tmp_path / "repo").resolve()
    report = json.loads(json.dumps(JEST))
    report["testResults"][0]["name"] = str(repo / "src" / "cart.test.js")
    report["testResults"] = report["testResults"][:1]
    _, results, _ = _import(tmp_path, {"out/jest.json": json.dumps(report)})
    assert "src/cart.test.js › later" in results


def test_vitest_json_is_labelled_vitest(tmp_path: Path) -> None:
    report = json.loads(json.dumps(JEST))
    for case in report["testResults"][0]["assertionResults"]:
        case["meta"] = {}
    _, results, _ = _import(tmp_path, {"out/vitest.json": json.dumps(report)})
    assert _label(results["cart.test.js › later"], "framework") == "vitest"


def test_trx(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/results.trx": TRX})

    assert imported.errors == ()
    assert {k: v["status"] for k, v in results.items()} == {
        "Shop.Tests.CartTests.AddsTax": "passed",
        "Shop.Tests.CartTests.RejectsNegative": "failed",
        "Shop.Tests.CartTests.Later": "skipped",
        "Shop.Tests.RowTests.Rows(1)": "passed",
        "Shop.Tests.RowTests.Rows(2)": "broken",
    }
    failed = results["Shop.Tests.CartTests.RejectsNegative"]
    assert failed["name"] == "RejectsNegative"
    assert failed["stop"] - failed["start"] == 1500
    assert failed["statusDetails"]["message"] == "Assert.Equal() Failure"
    assert "CartTests.cs:line 9" in failed["statusDetails"]["trace"]
    assert _label(failed, "framework") == "dotnet"
    assert _label(failed, "testClass") == "Shop.Tests.CartTests"


def test_trx_with_a_byte_order_mark(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/results.trx": b"\xef\xbb\xbf" + TRX.encode()})
    assert imported.errors == ()
    assert len(results) == 5


def test_go_test_json(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/go-test.json": _go_lines()})

    assert imported.errors == ()
    # The parent TestCart is left out: its failing subtest already counts.
    assert {k: v["status"] for k, v in results.items()} == {
        "example.com/shop.TestCart/tax": "passed",
        "example.com/shop.TestCart/negative": "failed",
        "example.com/shop.TestLater": "skipped",
        "example.com/broken.(package failed)": "broken",
    }
    failed = results["example.com/shop.TestCart/negative"]
    assert failed["name"] == "negative"
    assert failed["statusDetails"]["message"] == "cart_test.go:21: want error, got nil"
    assert "=== RUN" not in failed["statusDetails"]["trace"]
    assert _label(failed, "framework") == "go"
    assert (
        "undefined: x" in results["example.com/broken.(package failed)"]["statusDetails"]["trace"]
    )


def test_go_parent_that_failed_on_its_own_is_kept(tmp_path: Path) -> None:
    events = [
        {"Action": "pass", "Package": "p", "Test": "TestX/a", "Elapsed": 0.1},
        {
            "Action": "output",
            "Package": "p",
            "Test": "TestX",
            "Output": "    x_test.go:5: setup failed\n",
        },
        {"Action": "fail", "Package": "p", "Test": "TestX", "Elapsed": 0.2},
    ]
    text = "\n".join(json.dumps(e) for e in events)
    _, results, _ = _import(tmp_path, {"out/go.jsonl": text})
    assert {k: v["status"] for k, v in results.items()} == {
        "p.TestX/a": "passed",
        "p.TestX": "failed",
    }


def test_robot_output_xml(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/output.xml": ROBOT})

    assert imported.errors == ()
    assert {k: v["status"] for k, v in results.items()} == {
        "Tests.Login.Valid Login": "passed",
        "Tests.Login.Wrong Password": "failed",
        "Tests.Login.Later": "skipped",
    }
    passed = results["Tests.Login.Valid Login"]
    assert passed["stop"] - passed["start"] == 1250
    assert _label(passed, "tag") == "smoke"
    assert _label(passed, "framework") == "robot"
    assert results["Tests.Login.Wrong Password"]["statusDetails"]["message"] == (
        "Page should have contained 'Error'"
    )
    later = results["Tests.Login.Later"]
    assert later["stop"] - later["start"] == 100


def test_allure_results_folder(tmp_path: Path) -> None:
    result = {
        "uuid": "u1",
        "name": "adds tax",
        "fullName": "shop.CartTest.addsTax",
        "status": "failed",
        "start": 1000,
        "stop": 1500,
        "statusDetails": {"message": "expected 2"},
        "steps": [
            {"name": "open", "attachments": [{"name": "shot", "source": "s1-attachment.png"}]}
        ],
        "labels": [{"name": "framework", "value": "junit5"}],
    }
    files = {
        "allure-results/u1-result.json": json.dumps(result),
        "allure-results/s1-attachment.png": b"PNG",
        "allure-results/c1-container.json": json.dumps({"uuid": "c1", "children": ["u1"]}),
        "allure-results/environment.properties": "os=linux",
        "allure-results/categories.json": "[]",
    }
    imported, results, results_dir = _import(tmp_path, files, pattern="allure-results/*")

    assert imported.errors == ()
    assert (imported.tests, imported.failed, imported.attachments) == (1, 1, 1)
    stored = results["shop.CartTest.addsTax"]
    assert _label(stored, "framework") == "junit5"
    source = stored["steps"][0]["attachments"][0]["source"]
    assert source.endswith("-attachment.png")
    assert (results_dir / source).read_bytes() == b"PNG"


def test_allure_attachment_names_are_replaced(tmp_path: Path) -> None:
    """An attachment called ``page.html`` is stored under a ``-attachment`` name, which
    the API serves sandboxed, so it can't run script on the API's origin."""
    result = {
        "uuid": "u1",
        "name": "t",
        "fullName": "t",
        "status": "failed",
        "attachments": [
            {"name": "page", "source": "page.html", "type": "text/html"},
            {"name": "again", "source": "page.html", "type": "text/html"},
        ],
    }
    files = {
        "allure-results/u1-result.json": json.dumps(result),
        "allure-results/page.html": b"<script>alert(1)</script>",
    }
    imported, results, results_dir = _import(tmp_path, files, pattern="allure-results/*")

    assert imported.attachments == 1
    stored = results["t"]
    first, second = (a["source"] for a in stored["attachments"])
    assert first == second
    assert first.endswith("-attachment.html")
    assert not (results_dir / "page.html").exists()
    assert (results_dir / first).read_bytes() == b"<script>alert(1)</script>"


def test_unknown_files_are_reported_not_fatal(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/notes.txt": "hello", "out/odd.json": '{"x": 1}'})
    assert results == {}
    assert len(imported.errors) == 2
    assert any("neither XML nor JSON" in e for e in imported.errors)


def test_junit_still_works_next_to_other_formats(tmp_path: Path) -> None:
    junit = '<testsuite name="s"><testcase classname="a.B" name="c"/></testsuite>'
    imported, results, _ = _import(
        tmp_path,
        {"out/junit.xml": junit, "out/results.trx": TRX, "out/jest.json": json.dumps(JEST)},
    )
    assert imported.tests == 11
    assert "a.B.c" in results


RSPEC = {
    "version": "3.13.0",
    "examples": [
        {
            "id": "./spec/cart_spec.rb[1:1]",
            "description": "adds tax",
            "full_description": "Cart adds tax",
            "status": "passed",
            "file_path": "./spec/cart_spec.rb",
            "line_number": 4,
            "run_time": 0.012,
        },
        {
            "id": "./spec/cart_spec.rb[1:2]",
            "description": "rejects negative",
            "full_description": "Cart rejects negative",
            "status": "failed",
            "file_path": "./spec/cart_spec.rb",
            "line_number": 8,
            "run_time": 0.003,
            "exception": {
                "class": "RSpec::Expectations::ExpectationNotMetError",
                "message": "\nexpected: 2\n     got: 1\n",
                "backtrace": ["./spec/cart_spec.rb:9:in `block (2 levels)'"],
            },
        },
        {
            "id": "./spec/cart_spec.rb[1:3]",
            "description": "later",
            "full_description": "Cart later",
            "status": "pending",
            "file_path": "./spec/cart_spec.rb",
            "line_number": 12,
            "run_time": 0,
            "pending_message": "Not yet implemented",
        },
    ],
    "summary": {"duration": 0.02, "example_count": 3, "errors_outside_of_examples_count": 1},
    "messages": ["An error occurred while loading ./spec/broken_spec.rb."],
    "summary_line": "3 examples, 1 failure, 1 pending, 1 error occurred outside of examples",
}


def test_rspec_json(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/rspec.json": json.dumps(RSPEC)})

    assert imported.errors == ()
    assert {k: v["status"] for k, v in results.items()} == {
        "spec/cart_spec.rb › Cart adds tax": "passed",
        "spec/cart_spec.rb › Cart rejects negative": "failed",
        "spec/cart_spec.rb › Cart later": "skipped",
        "(errors outside of examples)": "broken",
    }
    failed = results["spec/cart_spec.rb › Cart rejects negative"]
    assert failed["name"] == "rejects negative"
    assert failed["statusDetails"]["message"] == (
        "RSpec::Expectations::ExpectationNotMetError: expected: 2"
    )
    assert "cart_spec.rb:9" in failed["statusDetails"]["trace"]
    assert _label(failed, "framework") == "rspec"
    assert _label(failed, "line") == "8"
    assert results["spec/cart_spec.rb › Cart later"]["statusDetails"]["message"] == (
        "Not yet implemented"
    )
    assert "broken_spec.rb" in results["(errors outside of examples)"]["statusDetails"]["trace"]


K6_EXPORT = {
    "metrics": {
        "http_req_duration": {"avg": 120.5, "p(95)": 640.2, "thresholds": {"p(95)<500": True}},
        "http_req_failed": {"value": 0.0, "thresholds": {"rate<0.01": False}},
        "iterations": {"count": 120},
    },
    "root_group": {
        "name": "",
        "path": "",
        "groups": {
            "checkout": {
                "name": "checkout",
                "path": "::checkout",
                "groups": {},
                "checks": {
                    "paid": {"name": "paid", "path": "::checkout::paid", "passes": 117, "fails": 3}
                },
            }
        },
        "checks": {"status is 200": {"name": "status is 200", "passes": 120, "fails": 0}},
    },
}

K6_HANDLE_SUMMARY = {
    "state": {"testRunDurationMs": 30898.9},
    "metrics": {
        "http_req_duration": {
            "type": "trend",
            "values": {"avg": 120.5, "p(95)": 410.0},
            "thresholds": {"p(95)<500": {"ok": True}},
        },
        "checks": {
            "type": "rate",
            "values": {"rate": 0.9},
            "thresholds": {"rate>0.95": {"ok": False}},
        },
    },
    "root_group": {
        "name": "",
        "path": "",
        "groups": [],
        "checks": [{"name": "status is 200", "path": "::status is 200", "passes": 9, "fails": 1}],
    },
}


def test_k6_summary_export(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/k6-summary.json": json.dumps(K6_EXPORT)})

    assert imported.errors == ()
    assert {k: v["status"] for k, v in results.items()} == {
        "checks › status is 200": "passed",
        "checks › checkout › paid": "failed",
        "thresholds › http_req_duration › p(95)<500": "failed",
        "thresholds › http_req_failed › rate<0.01": "passed",
    }
    assert results["checks › checkout › paid"]["statusDetails"]["message"] == "3 of 120 failed"
    crossed = results["thresholds › http_req_duration › p(95)<500"]
    assert "p(95)=640.2" in crossed["statusDetails"]["message"]
    assert _label(crossed, "framework") == "k6"
    assert crossed["stop"] == crossed["start"]


def test_k6_handle_summary(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/summary.json": json.dumps(K6_HANDLE_SUMMARY)})

    assert imported.errors == ()
    assert {k: v["status"] for k, v in results.items()} == {
        "checks › status is 200": "failed",
        "thresholds › checks › rate>0.95": "failed",
        "thresholds › http_req_duration › p(95)<500": "passed",
    }
    assert imported.failed == 2


XCRESULT = {
    "devices": [{"deviceName": "iPhone 16", "platform": "iOS Simulator"}],
    "testPlanConfigurations": [{"configurationId": "1", "configurationName": "Default"}],
    "testNodes": [
        {
            "nodeType": "Test Plan",
            "name": "Shop",
            "result": "Failed",
            "children": [
                {
                    "nodeType": "Unit test bundle",
                    "name": "ShopTests",
                    "result": "Failed",
                    "children": [
                        {
                            "nodeType": "Test Suite",
                            "name": "CartTests",
                            "nodeIdentifier": "CartTests",
                            "result": "Failed",
                            "children": [
                                {
                                    "nodeType": "Test Case",
                                    "name": "testAddsTax()",
                                    "nodeIdentifier": "CartTests/testAddsTax()",
                                    "result": "Passed",
                                    "duration": "0.12s",
                                },
                                {
                                    "nodeType": "Test Case",
                                    "name": "testRejectsNegative()",
                                    "nodeIdentifier": "CartTests/testRejectsNegative()",
                                    "result": "Failed",
                                    "durationInSeconds": 0.5,
                                    "children": [
                                        {
                                            "nodeType": "Failure Message",
                                            "name": "CartTests.swift:21: XCTAssertEqual failed",
                                            "result": "Failed",
                                        }
                                    ],
                                },
                                {
                                    "nodeType": "Test Case",
                                    "name": "testRetried()",
                                    "nodeIdentifier": "CartTests/testRetried()",
                                    "result": "Passed",
                                    "duration": "1m 2s",
                                    "children": [
                                        {"nodeType": "Repetition", "name": "1", "result": "Failed"},
                                        {"nodeType": "Repetition", "name": "2", "result": "Passed"},
                                    ],
                                },
                                {
                                    "nodeType": "Test Case",
                                    "name": "testLater()",
                                    "nodeIdentifier": "CartTests/testLater()",
                                    "result": "Skipped",
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ],
}


def test_xcresult_json(tmp_path: Path) -> None:
    imported, results, _ = _import(tmp_path, {"out/xcresult.json": json.dumps(XCRESULT)})

    assert imported.errors == ()
    assert {k: v["status"] for k, v in results.items()} == {
        "ShopTests/CartTests/testAddsTax()": "passed",
        "ShopTests/CartTests/testRejectsNegative()": "failed",
        "ShopTests/CartTests/testRetried()": "passed",
        "ShopTests/CartTests/testLater()": "skipped",
    }
    assert imported.flaky == 1
    passed = results["ShopTests/CartTests/testAddsTax()"]
    assert passed["stop"] - passed["start"] == 120
    failed = results["ShopTests/CartTests/testRejectsNegative()"]
    assert failed["stop"] - failed["start"] == 500
    assert failed["statusDetails"]["message"] == "CartTests.swift:21: XCTAssertEqual failed"
    assert _label(failed, "framework") == "xctest"
    assert _label(failed, "suite") == "ShopTests"
    assert results["ShopTests/CartTests/testRetried()"]["statusDetails"]["flaky"] is True


ANDROID_JUNIT = """<?xml version='1.0' encoding='UTF-8' ?>
<testsuite name="com.acme.CartTest" tests="2" failures="1" errors="0" skipped="0" time="1.2"
    timestamp="2026-10-26T10:00:00" hostname="localhost">
  <properties>
    <property name="device" value="Pixel_8(AVD) - 15" />
    <property name="flavor" value="" />
    <property name="project" value=":app" />
  </properties>
  <testcase name="addsTax" classname="com.acme.CartTest" time="0.4" />
  <testcase name="opensCheckout" classname="com.acme.CartTest" time="0.8">
    <failure>androidx.test.espresso.NoMatchingViewException: No views in hierarchy</failure>
  </testcase>
</testsuite>
"""


def test_android_instrumentation_junit(tmp_path: Path) -> None:
    imported, results, _ = _import(
        tmp_path, {"out/TEST-Pixel_8(AVD) - 15-_app-.xml": ANDROID_JUNIT}
    )

    assert imported.errors == ()
    assert {k: v["status"] for k, v in results.items()} == {
        "com.acme.CartTest.addsTax": "passed",
        "com.acme.CartTest.opensCheckout": "failed",
    }
    failed = results["com.acme.CartTest.opensCheckout"]
    assert _label(failed, "framework") == "android"
    assert _label(failed, "host") == "Pixel_8(AVD) - 15"


def test_plain_junit_keeps_the_stage_tool_as_framework(tmp_path: Path) -> None:
    junit = '<testsuite name="s"><testcase classname="a.B" name="c"/></testsuite>'
    _, results, _ = _import(tmp_path, {"out/junit.xml": junit}, tool="maestro")
    assert _label(results["a.B.c"], "framework") == "maestro"
    assert _label(results["a.B.c"], "host") is None
