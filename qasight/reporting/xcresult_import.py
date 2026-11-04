"""Convert Xcode test results (``xcresulttool`` JSON) into Allure ``*-result.json`` dicts.

``xcodebuild test`` writes an ``.xcresult`` bundle, a database only Apple's
``xcresulttool`` can read. Xcode 16 and later export its tests as JSON, with
no extra package:

    xcrun xcresulttool get test-results tests --path Tests.xcresult > xcresult.json

That file is a tree of ``testNodes`` (test plan › bundle › suite › test case),
each with a ``nodeType``, a ``result`` and, for test cases, a duration. XCTest
and Swift Testing both appear in it.

Status mapping:

    Passed              -> passed
    Failed              -> failed
    Skipped             -> skipped
    Expected Failure    -> passed

A test case's first ``Failure Message`` child is its message, all of them its
trace. A test that failed then passed on a repetition (``-retry-tests-on-failure``)
is passed and flagged flaky. ``full_name`` is ``Bundle/Suite/test()``, the
``-only-testing:`` form ``xcodebuild`` takes.
"""

from __future__ import annotations

import re
from typing import Any

from qasight.reporting.imported_results import make_result

_STATUS = {
    "Passed": "passed",
    "Failed": "failed",
    "Skipped": "skipped",
    "Expected Failure": "passed",
}
_BUNDLES = ("Unit test bundle", "UI test bundle")
_DURATION = re.compile(r"([\d.]+)\s*(ms|s|m)?")


def is_xcresult_report(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("testNodes"), list)
        and ("devices" in data or "testPlanConfigurations" in data)
    )


def _duration_ms(node: dict[str, Any]) -> int:
    seconds = node.get("durationInSeconds")
    try:
        if isinstance(seconds, (int, float)):
            return max(0, int(round(seconds * 1000)))
        match = _DURATION.match(str(node.get("duration") or "").strip())
        if not match:
            return 0
        value = float(match.group(1))
        unit = match.group(2) or "s"
        return max(0, int(round(value * {"ms": 1, "s": 1000, "m": 60_000}[unit])))
    except (ValueError, OverflowError):  # "1.2.3s", inf: no time rather than no tests
        return 0


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in node.get("children") or [] if isinstance(c, dict)]


def _descendants(node: dict[str, Any], node_type: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for child in _children(node):
        if child.get("nodeType") == node_type:
            found.append(child)
        found += _descendants(child, node_type)
    return found


def _test_cases(
    node: dict[str, Any], bundle: str, suites: tuple[str, ...]
) -> list[tuple[str, tuple[str, ...], dict[str, Any]]]:
    found: list[tuple[str, tuple[str, ...], dict[str, Any]]] = []
    for child in _children(node):
        kind = child.get("nodeType")
        name = str(child.get("name") or "")
        if kind == "Test Case":
            found.append((bundle, suites, child))
        elif kind in _BUNDLES:
            found += _test_cases(child, name, ())
        elif kind == "Test Suite":
            found += _test_cases(child, bundle, (*suites, name))
        else:  # Test Plan, Device, configurations: containers that add no name
            found += _test_cases(child, bundle, suites)
    return found


def xcresult_results_for_report(
    data: dict[str, Any], *, tool: str, fallback_ms: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    clock = fallback_ms
    for root in data["testNodes"]:
        if not isinstance(root, dict):
            continue
        for bundle, suites, case in _test_cases({"children": [root]}, "", ()):
            name = str(case.get("name") or "(unnamed)")
            status = _STATUS.get(str(case.get("result") or ""), "broken")
            failures = [str(f.get("name") or "") for f in _descendants(case, "Failure Message")]
            runs = [str(r.get("result") or "") for r in _descendants(case, "Repetition")]
            flaky = status == "passed" and "Failed" in runs
            identifier = str(case.get("nodeIdentifier") or "/".join((*suites, name)))
            full_name = f"{bundle}/{identifier}" if bundle else identifier
            duration = _duration_ms(case)
            labels = [{"name": "subSuite", "value": "/".join(suites)}] if suites else []
            results.append(
                make_result(
                    tool=tool,
                    framework="xctest",
                    language="swift",
                    name=name,
                    full_name=full_name,
                    status=status,
                    start_ms=clock,
                    duration_ms=duration,
                    suite=bundle or "xctest",
                    message=failures[0] if failures and status != "passed" else None,
                    trace="\n".join(failures) if failures and status != "passed" else None,
                    labels=labels,
                    flaky=flaky,
                )
            )
            clock += duration
    return results
