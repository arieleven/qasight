"""Convert a Jest or Vitest JSON report into Allure ``*-result.json`` dicts.

Both runners write the same JSON with no extra package: ``jest --json
--outputFile=results.json`` and ``vitest run --reporter=json
--outputFile=results.json``. JUnit output, by contrast, needs ``jest-junit``
for Jest. The JSON also keeps what JUnit drops: the ``describe`` blocks a test
sits in, tests Jest retried (``retryReasons``), and test files that failed
before any test ran (a syntax error, a failed import).

Status mapping:

    passed                              -> passed (flaky when Jest retried it)
    failed                              -> failed
    skipped, pending, todo, disabled    -> skipped
    a file that failed with no tests    -> broken, one result for the file
    a file that failed, no test failing -> broken, one more result for the file
                                           (a ``beforeAll``/``afterAll`` hook threw)

Test file paths are made relative to the repository root when they sit inside
it, else to the folder all of the report's test files share, so a report made
on a CI machine gets the same test ids as one made locally.
"""

from __future__ import annotations

import json
import ntpath
import os
import re
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any

from qasight.reporting.imported_results import make_result

_STATUS = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
    "pending": "skipped",
    "todo": "skipped",
    "disabled": "skipped",
    "focused": "passed",
}


def is_jest_report(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("testResults"), list)


def _framework(report: dict, tool: str) -> str:
    if tool in ("jest", "vitest"):
        return tool
    for file_result in report.get("testResults") or []:
        if not isinstance(file_result, dict):
            continue
        cases = file_result.get("assertionResults")
        for case in cases if isinstance(cases, list) else []:
            # Vitest adds ``meta`` to every assertion; Jest never does.
            if isinstance(case, dict) and "meta" in case:
                return "vitest"
    return "jest"


# ``C:\\ci\\repo\\a.test.js``, ``C:/ci/...`` or ``\\\\server\\share\\...``: absolute on Windows.
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _relative_paths(names: list[str], root: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    outside: list[str] = []
    windows: list[str] = []
    for name in names:
        path = Path(name)
        if root is not None and path.is_absolute():
            try:
                out[name] = path.resolve().relative_to(root).as_posix()
                continue
            except (ValueError, OSError):
                pass
        if path.is_absolute():
            outside.append(name)
        elif _WINDOWS_ABSOLUTE.match(name):
            # A report made on Windows read here: not absolute to this OS, but it is.
            windows.append(name)
        else:
            out[name] = PurePath(name).as_posix()
    if outside:
        common = os.path.commonpath([str(PurePath(n).parent) for n in outside])
        for name in outside:
            out[name] = PurePath(os.path.relpath(name, common)).as_posix()
    if windows:
        try:
            common = ntpath.commonpath([ntpath.dirname(n) for n in windows])
        except ValueError:  # different drives
            common = ""
        for name in windows:
            rel = ntpath.relpath(name, common) if common else ntpath.splitdrive(name)[1]
            out[name] = PureWindowsPath(rel).as_posix().lstrip("/")
    return out


def jest_results_for_file(path: Path, *, tool: str, root: Path | None) -> list[dict[str, Any]]:
    """Allure result dicts for one Jest/Vitest JSON report; :class:`ValueError` if it isn't one."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON ({exc})") from exc
    if not is_jest_report(report):
        raise ValueError("not a Jest/Vitest JSON report (no 'testResults' list)")
    return jest_results_for_report(
        report, tool=tool, root=root, fallback_ms=int(path.stat().st_mtime * 1000)
    )


def jest_results_for_report(
    report: dict, *, tool: str, root: Path | None, fallback_ms: int
) -> list[dict[str, Any]]:
    framework = _framework(report, tool)
    files = [f for f in report.get("testResults") or [] if isinstance(f, dict)]
    rel = _relative_paths([str(f.get("name") or "") for f in files if f.get("name")], root)
    report_start = report.get("startTime")
    if not isinstance(report_start, (int, float)):
        report_start = fallback_ms
    results: list[dict[str, Any]] = []
    for file_result in files:
        file_name = rel.get(str(file_result.get("name") or ""), "") or "(unknown file)"
        clock = file_result.get("startTime")
        clock = int(clock) if isinstance(clock, (int, float)) and clock > 0 else int(report_start)
        raw_cases = file_result.get("assertionResults")
        cases = [c for c in raw_cases if isinstance(c, dict)] if isinstance(raw_cases, list) else []
        if not cases:
            file_message = str(
                file_result.get("message") or file_result.get("failureMessage") or ""
            )
            if file_result.get("status") == "failed" or file_message.strip():
                results.append(
                    make_result(
                        tool=tool,
                        framework=framework,
                        language="javascript",
                        name="(test file failed to run)",
                        full_name=f"{file_name} › (test file failed to run)",
                        status="broken",
                        start_ms=clock,
                        duration_ms=0,
                        suite=file_name,
                        message=file_message or "The test file failed before any test ran",
                        trace=file_message or None,
                    )
                )
            continue
        file_failed = file_result.get("status") == "failed"
        any_case_failed = False
        for case in cases:
            title = str(case.get("title") or "(unnamed)")
            raw_ancestors = case.get("ancestorTitles")
            if not isinstance(raw_ancestors, list):
                raw_ancestors = [raw_ancestors] if raw_ancestors else []
            ancestors = [str(a) for a in raw_ancestors if str(a)]
            status = _STATUS.get(str(case.get("status")), "broken")
            failures = "\n\n".join(str(m) for m in case.get("failureMessages") or [] if m)
            retries = [str(r) for r in case.get("retryReasons") or [] if r]
            flaky = status == "passed" and bool(retries)
            message: str | None = failures or None
            if flaky:
                message = "Passed on retry. " + retries[0]
            try:
                duration = int(round(float(case.get("duration") or 0)))
            except (TypeError, ValueError, OverflowError):
                duration = 0
            result = make_result(
                tool=tool,
                framework=framework,
                language="javascript",
                name=title,
                full_name=" › ".join([file_name, *ancestors, title]),
                status=status,
                start_ms=clock,
                duration_ms=duration,
                suite=file_name,
                message=message,
                trace=failures or None,
                labels=[{"name": "subSuite", "value": " › ".join(ancestors)}] if ancestors else [],
                flaky=flaky,
            )
            results.append(result)
            clock += max(0, duration)
            any_case_failed = any_case_failed or status in ("failed", "broken")
        if file_failed and not any_case_failed:
            # Jest and Vitest fail the file, not a test, when a hook throws: the
            # tests show passed or skipped, and only the file's message says why.
            file_message = str(
                file_result.get("message") or file_result.get("failureMessage") or ""
            )
            results.append(
                make_result(
                    tool=tool,
                    framework=framework,
                    language="javascript",
                    name="(test file failed)",
                    full_name=f"{file_name} › (test file failed)",
                    status="broken",
                    start_ms=clock,
                    duration_ms=0,
                    suite=file_name,
                    message=_first_line(file_message) or "The test file failed outside its tests",
                    trace=file_message or None,
                )
            )
    return results


def _first_line(text: str) -> str | None:
    """The first non-blank line, without Jest's ``●`` bullet."""
    for line in text.splitlines():
        line = line.strip().lstrip("●").strip()
        if line:
            return line
    return None
