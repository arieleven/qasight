"""Odd and malformed result files: no crash, no silent drop, no lost neighbour.

Found by feeding the importers truncated, mistyped, re-encoded and
other-tool-version files (2026-10-31). Each test is one case that used to
crash the whole import, count 0 tests without a word, or count wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.reporting.junit_import import import_junit_reports

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _import(tmp_path: Path, files: dict[str, str | bytes], pattern: str = "*"):  # noqa: ANN202
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    for name, content in files.items():
        path = repo / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    results_dir = tmp_path / "results"
    imported = import_junit_reports(
        target_repo=repo, patterns=(pattern,), results_dir=results_dir, tool="t"
    )
    results = {}
    for path in results_dir.glob("*-result.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        results[data["fullName"]] = data
    return imported, results


GOOD = '<testsuite name="s"><testcase classname="s" name="ok"/></testsuite>'


@pytest.mark.parametrize(
    "bad",
    [
        json.dumps({"testResults": ["str", 5, {"name": 5, "assertionResults": 7}]}),
        json.dumps({"suites": ["s", {"specs": ["x", {"tests": ["y"]}]}]}),
        json.dumps(
            {
                "suites": [
                    {
                        "title": "f",
                        "specs": [
                            {
                                "title": "s",
                                "tests": [
                                    {
                                        "status": "unexpected",
                                        "results": [
                                            {"status": "failed", "error": {"message": {"a": 1}}}
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "errors": [{"message": 5}],
            }
        ),
        json.dumps({"examples": [{"status": "pending", "pending_message": 5}], "summary": {}}),
        json.dumps({"metrics": {}, "root_group": {"checks": [{"name": "c", "passes": [1]}]}}),
        '<testsuite><testcase name="a" time="inf"/></testsuite>',
    ],
    ids=["jest-types", "playwright-types", "playwright-error-types", "rspec", "k6", "junit-inf"],
)
def test_odd_types_never_crash_or_lose_the_other_files(tmp_path: Path, bad: str) -> None:
    name = "bad.xml" if bad.startswith("<") else "bad.json"
    imported, results = _import(tmp_path, {"good.xml": GOOD, name: bad})
    assert "s.ok" in results


def test_junit_infinite_time_is_zero(tmp_path: Path) -> None:
    _, results = _import(
        tmp_path, {"r.xml": '<testsuite><testcase name="a" time="inf"/></testsuite>'}
    )
    (result,) = results.values()
    assert result["stop"] == result["start"]


def test_nested_testsuite_under_a_root_testsuite(tmp_path: Path) -> None:
    xml = (
        '<testsuite name="outer"><testcase name="top"/>'
        '<testsuite name="inner"><testcase name="deep"><failure message="m"/></testcase>'
        "</testsuite></testsuite>"
    )
    imported, results = _import(tmp_path, {"r.xml": xml})
    assert (imported.tests, imported.failed) == (2, 1)
    assert set(results) == {"outer.top", "inner.deep"}


def test_utf16_junit(tmp_path: Path) -> None:
    xml = '<?xml version="1.0" encoding="UTF-16"?><testsuite name="s"><testcase name="a"/></testsuite>'
    imported, _ = _import(tmp_path, {"r.xml": xml.encode("utf-16")})
    assert imported.tests == 1 and imported.errors == ()


def test_xml_that_is_not_junit_is_reported(tmp_path: Path) -> None:
    imported, _ = _import(tmp_path, {"r.xml": "<html><body>502 Bad Gateway</body></html>"})
    assert imported.tests == 0
    assert imported.unreadable == 1
    assert "not a JUnit report (root element <html>" in imported.errors[0]


def test_a_report_named_like_an_attachment_is_still_read(tmp_path: Path) -> None:
    imported, _ = _import(
        tmp_path, {"email-attachment-tests.xml": GOOD, "0a1b2c3d-attachment.png": b"PNG"}
    )
    assert imported.tests == 1 and imported.errors == ()


def test_surefire_rerun_pass_is_flaky(tmp_path: Path) -> None:
    xml = (
        '<testsuite name="s"><testcase classname="C" name="a">'
        '<flakyFailure message="first try" type="AssertionError">trace 1</flakyFailure>'
        "</testcase></testsuite>"
    )
    imported, results = _import(tmp_path, {"r.xml": xml})
    result = results["C.a"]
    assert result["status"] == "passed"
    assert result["statusDetails"]["flaky"] is True
    assert result["statusDetails"]["message"] == "Passed on rerun. first try"
    assert imported.flaky == 1


def test_junit_message_is_clipped(tmp_path: Path) -> None:
    xml = (
        f'<testsuite><testcase name="a"><failure message="{"M" * 50_000}"/></testcase></testsuite>'
    )
    _, results = _import(tmp_path, {"r.xml": xml})
    (result,) = results.values()
    assert len(result["statusDetails"]["message"]) < 21_000


def test_jest_ancestor_titles_as_a_string(tmp_path: Path) -> None:
    report = {
        "testResults": [
            {
                "name": "a.test.js",
                "assertionResults": [{"title": "t", "status": "passed", "ancestorTitles": "Cart"}],
            }
        ]
    }
    _, results = _import(tmp_path, {"r.json": json.dumps(report)})
    assert list(results) == ["a.test.js › Cart › t"]


ROBOT_TEARDOWN = """<?xml version="1.0" encoding="UTF-8"?>
<robot generator="Robot 7.1">
<suite id="s1" name="Shop">
<suite id="s1-s1" name="Cart">
<test id="s1-s1-t1" name="Adds">
<status status="PASS" start="2026-10-31T10:00:00.000" elapsed="0.1"/>
</test>
<test id="s1-s1-t2" name="Later">
<status status="SKIP" start="2026-10-31T10:00:00.100" elapsed="0.1">not now</status>
</test>
<kw name="Fail" owner="BuiltIn" type="TEARDOWN">
<status status="FAIL" start="2026-10-31T10:00:00.200" elapsed="0.1">teardown boom</status>
</kw>
<status status="FAIL" start="2026-10-31T10:00:00.000" elapsed="0.3">Suite teardown failed:
teardown boom</status>
</suite>
<suite id="s1-s2" name="Pay">
<test id="s1-s2-t1" name="Charges">
<status status="PASS" start="2026-10-31T10:00:01.000" elapsed="0.1"/>
</test>
<status status="PASS" start="2026-10-31T10:00:01.000" elapsed="0.1"/>
</suite>
<status status="FAIL" start="2026-10-31T10:00:00.000" elapsed="1.1"/>
</suite>
</robot>
"""


def test_robot_suite_teardown_failure_fails_its_passed_tests(tmp_path: Path) -> None:
    imported, results = _import(tmp_path, {"output.xml": ROBOT_TEARDOWN})
    # What `robot` itself reports: Adds fails, Later stays skipped, Pay is untouched.
    assert {k: v["status"] for k, v in results.items()} == {
        "Shop.Cart.Adds": "failed",
        "Shop.Cart.Later": "skipped",
        "Shop.Pay.Charges": "passed",
    }
    assert results["Shop.Cart.Adds"]["statusDetails"]["message"] == "Parent suite teardown failed:"
    assert "teardown boom" in results["Shop.Cart.Adds"]["statusDetails"]["trace"]
    assert imported.failed == 1
