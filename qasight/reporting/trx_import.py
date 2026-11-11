"""Convert a .NET TRX file (``dotnet test --logger trx``) into Allure ``*-result.json`` dicts.

TRX is what ``dotnet test`` and Visual Studio write by default, for xUnit,
NUnit and MSTest alike, so .NET suites need no extra JUnit logger package.

Status mapping (``UnitTestResult/@outcome``):

    Passed, PassedButRunAborted         -> passed
    Failed                              -> failed
    Error, Timeout, Aborted             -> broken
    NotExecuted, Inconclusive, other    -> skipped

A data-driven test's rows (``InnerResults``) are one result each. The test's
class comes from ``TestDefinitions``, so ``full_name`` is ``Namespace.Class.Method``
plus any row arguments, the way ``dotnet test --filter`` names it.

A run-level error (``ResultSummary/RunInfos/RunInfo[@outcome="Error"]``: the
test host crashed, the run was aborted) is one ``broken`` result, so a run
that died before or between tests is never recorded as passed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405 - types only; parsing goes through defusedxml
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as SafeET

from qasight.reporting.imported_results import iso_ms, make_result

_STATUS = {
    "passed": "passed",
    "passedbutrunaborted": "passed",
    "failed": "failed",
    "error": "broken",
    "timeout": "broken",
    "aborted": "broken",
}
_DURATION = re.compile(r"^(?:(\d+)\.)?(\d+):(\d+):(\d+(?:\.\d+)?)$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def is_trx_root(root: ET.Element) -> bool:
    return _local(root.tag) == "TestRun"


def _duration_ms(value: str | None) -> int:
    match = _DURATION.match((value or "").strip())
    if not match:
        return 0
    days, hours, minutes, seconds = match.groups()
    total = int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return int(round(total * 1000))


def _child(node: ET.Element, *names: str) -> ET.Element | None:
    for name in names:
        found = next((c for c in node if _local(c.tag) == name), None)
        if found is None:
            return None
        node = found
    return node


def _text(node: ET.Element | None) -> str | None:
    return node.text if node is not None and node.text else None


def _classes(root: ET.Element) -> dict[str, tuple[str, str]]:
    """testId -> (className, method name) from ``TestDefinitions``."""
    out: dict[str, tuple[str, str]] = {}
    definitions = _child(root, "TestDefinitions")
    for test in definitions if definitions is not None else []:
        method = _child(test, "TestMethod")
        test_id = test.get("id")
        if method is not None and test_id:
            out[test_id] = (method.get("className") or "", method.get("name") or "")
    return out


def trx_results_for_root(root: ET.Element, *, tool: str, fallback_ms: int) -> list[dict[str, Any]]:
    classes = _classes(root)
    results_node = _child(root, "Results")
    times = _child(root, "Times")
    run_start = iso_ms(times.get("start") if times is not None else None, fallback_ms)
    out: list[dict[str, Any]] = []

    def visit(node: ET.Element, inherited_id: str | None) -> None:
        test_id = node.get("testId") or inherited_id or ""
        inner = _child(node, "InnerResults")
        if inner is not None and len(inner):
            for row in inner:
                visit(row, test_id)
            return
        class_name, method = classes.get(test_id, ("", ""))
        # Fully qualified class names may carry a ", Assembly" suffix in older TRX.
        class_name = class_name.split(",", 1)[0].strip()
        test_name = node.get("testName") or method or "(unnamed)"
        if class_name and test_name.startswith(class_name + "."):
            test_name = test_name[len(class_name) + 1 :]
        full_name = f"{class_name}.{test_name}" if class_name else test_name
        status = _STATUS.get((node.get("outcome") or "").lower(), "skipped")
        error = _child(node, "Output", "ErrorInfo")
        message = _text(_child(error, "Message")) if error is not None else None
        stack = _text(_child(error, "StackTrace")) if error is not None else None
        if status == "skipped" and message is None:
            message = _text(_child(node, "Output", "StdOut"))
        trace = "\n".join(t for t in (message, stack) if t) or None
        suite = class_name.rsplit(".", 1)[0] if "." in class_name else (class_name or "dotnet")
        out.append(
            make_result(
                tool=tool,
                framework="dotnet",
                language="csharp",
                name=test_name,
                full_name=full_name,
                status=status,
                start_ms=iso_ms(node.get("startTime"), run_start),
                duration_ms=_duration_ms(node.get("duration")),
                suite=suite,
                message=message,
                trace=trace,
                labels=[{"name": "testClass", "value": class_name}] if class_name else [],
            )
        )

    for node in results_node if results_node is not None else []:
        if _local(node.tag).endswith("TestResult"):
            visit(node, None)
    out += _run_errors(root, tool=tool, start_ms=run_start)
    return out


def _run_errors(root: ET.Element, *, tool: str, start_ms: int) -> list[dict[str, Any]]:
    """One ``broken`` result when the run itself reported errors (host crash, abort)."""
    infos = _child(root, "ResultSummary", "RunInfos")
    texts = [
        (_text(_child(info, "Text")) or "").strip()
        for info in (infos if infos is not None else [])
        if (info.get("outcome") or "").lower() == "error"
    ]
    if not texts:
        return []
    trace = "\n\n".join(t for t in texts if t) or None
    return [
        make_result(
            tool=tool,
            framework="dotnet",
            language="csharp",
            name="(test run error)",
            full_name="(test run error)",
            status="broken",
            start_ms=start_ms,
            duration_ms=0,
            suite="dotnet",
            message=trace or "The test run reported an error",
            trace=trace,
        )
    ]


def trx_results_for_file(path: Path, *, tool: str) -> list[dict[str, Any]]:
    """Allure result dicts for one TRX file; :class:`ValueError` if it isn't one."""
    root = SafeET.parse(path).getroot()
    if not is_trx_root(root):
        raise ValueError("not a TRX file (no <TestRun> root)")
    return trx_results_for_root(root, tool=tool, fallback_ms=int(path.stat().st_mtime * 1000))
