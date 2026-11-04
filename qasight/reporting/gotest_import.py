"""Convert ``go test -json`` output into Allure ``*-result.json`` dicts.

``go test -json ./... > go-test.json`` is built into Go, so Go suites need no
``gotestsum`` or ``go-junit-report``. The file is one JSON event per line
(``{"Action": "pass", "Package": "...", "Test": "TestX/case", "Elapsed": 0.01}``);
lines that aren't events (build output) are ignored.

What becomes a result:

- every test that ran to ``pass``, ``fail`` or ``skip``, named
  ``<package>.<Test>/<subtest>``;
- a parent test only when it has no subtests, or failed while none of its
  subtests did (otherwise its subtests already tell the story, and counting the
  parent too would report one failure twice);
- a test that started but never finished in a package that failed (it hit
  the ``-timeout`` or the binary crashed): ``broken``;
- a package that failed with no failing test (a build error, a panic in
  ``TestMain``): one ``broken`` result for the package. Go 1.24 and later
  report the compiler's output as ``build-output`` events keyed by
  ``ImportPath``; that output becomes the package result's trace.

A test's output lines become its trace; the first ``file.go:N:`` line is its message.

A test that ran more than once (``go test -count=3``) is one result: a failure
followed by a final pass is flaky, otherwise the worst run wins.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qasight.reporting.imported_results import iso_ms, make_result

_STATUS = {"pass": "passed", "fail": "failed", "skip": "skipped"}
_LOCATION = re.compile(r"^\s*[\w./-]+\.go:\d+:")
_HEADER = re.compile(r"^\s*(=== (RUN|PAUSE|CONT|NAME)|--- (PASS|FAIL|SKIP))")


@dataclass
class _Test:
    package: str
    name: str
    start_ms: int
    action: str | None = None
    elapsed_ms: int = 0
    output: list[str] = field(default_factory=list)
    attempts: list[str] = field(default_factory=list)


def _events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("Action"), str):
            events.append(event)
    return events


def _import_package(import_path: Any) -> str:
    """``example.com/m`` from a Go 1.24 build event's ``"example.com/m [example.com/m.test]"``."""
    return str(import_path or "").split(" [", 1)[0]


def is_gotest_text(text: str) -> bool:
    """True when the first JSON line of ``text`` is a ``go test -json`` event."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return False
        # Go 1.24+: a build failure comes first, as ``ImportPath`` events.
        return (
            isinstance(event, dict)
            and "Action" in event
            and ("Package" in event or "ImportPath" in event)
        )
    return False


def _elapsed_ms(value: Any) -> int:
    try:
        return max(0, int(round(float(value or 0) * 1000)))
    except (TypeError, ValueError, OverflowError):
        return 0


def gotest_results_for_text(text: str, *, tool: str, fallback_ms: int) -> list[dict[str, Any]]:
    tests: dict[tuple[str, str], _Test] = {}
    packages: dict[str, _Test] = {}
    build_output: dict[str, list[str]] = {}
    for event in _events(text):
        action = str(event["Action"])
        if "Package" not in event and "ImportPath" in event:
            if action == "build-output":
                package = _import_package(event.get("ImportPath"))
                build_output.setdefault(package, []).append(str(event.get("Output") or ""))
            continue
        package = str(event.get("Package") or "")
        name = event.get("Test")
        stamp = iso_ms(event.get("Time"), fallback_ms)
        if name:
            key = (package, str(name))
            test = tests.setdefault(key, _Test(package, str(name), stamp))
        else:
            test = packages.setdefault(package, _Test(package, "", stamp))
        if action == "output":
            test.output.append(str(event.get("Output") or ""))
        elif action in _STATUS:
            test.action = action
            test.elapsed_ms += _elapsed_ms(event.get("Elapsed"))
            test.attempts.append(action)

    finished = {k: t for k, t in tests.items() if t.action in _STATUS}
    parents = {(p, n.rsplit("/", 1)[0]) for p, n in finished if "/" in n}
    failing_children = {
        (p, n.rsplit("/", 1)[0])
        for (p, n), t in finished.items()
        if "/" in n and _final_action(t)[0] == "fail"
    }

    results: list[dict[str, Any]] = []
    for key, test in finished.items():
        if key in parents and not (
            _final_action(test)[0] == "fail" and key not in failing_children
        ):
            continue
        results.append(_result(test, tool=tool))
    # Any failed run fails the package, even when a later run passed (flaky).
    failed_packages = {p for (p, _), t in finished.items() if "fail" in t.attempts}
    for (package, _), test in tests.items():
        # Started, never finished, and its package failed: the -timeout or a crash.
        if test.action is None and (packages.get(package) or _Test("", "", 0)).action == "fail":
            unfinished = [line for line in test.output if line.strip()]
            unfinished.append(f"{test.name} did not finish: the test binary timed out or crashed\n")
            results.append(
                _result(
                    _Test(package, test.name, test.start_ms, "fail", 0, unfinished),
                    tool=tool,
                    status="broken",
                )
            )
            failed_packages.add(package)
    for package, pkg in packages.items():
        if pkg.action == "fail" and package not in failed_packages:
            results.append(
                _result(
                    _Test(
                        package,
                        "(package failed)",
                        pkg.start_ms,
                        "fail",
                        pkg.elapsed_ms,
                        [*build_output.get(package, []), *pkg.output],
                    ),
                    tool=tool,
                    status="broken",
                )
            )
    return results


def _final_action(test: _Test) -> tuple[str, bool]:
    """The action a test that ran ``len(attempts)`` times ends with, and whether it was flaky."""
    runs = test.attempts or [str(test.action)]
    if "fail" in runs and runs[-1] == "pass":
        return "pass", True
    for action in ("fail", "pass", "skip"):
        if action in runs:
            return action, False
    return str(test.action), False


def _result(test: _Test, *, tool: str, status: str | None = None) -> dict[str, Any]:
    action, flaky = _final_action(test)
    lines = [line.rstrip("\n") for line in test.output]
    body = [line for line in lines if not _HEADER.match(line)]
    message = next((line.strip() for line in body if _LOCATION.match(line)), None)
    if message is None and (action != "pass" or flaky):
        message = next((line.strip() for line in body if line.strip()), None)
    trace = "\n".join(body).strip() or None
    leaf = test.name.rsplit("/", 1)[-1]
    return make_result(
        tool=tool,
        framework="go",
        language="go",
        name=leaf,
        full_name=f"{test.package}.{test.name}" if test.package else test.name,
        status=status or _STATUS[action],
        start_ms=test.start_ms,
        duration_ms=test.elapsed_ms,
        suite=test.package or "go",
        message=(f"Passed on rerun. {message or ''}".strip() if flaky else message)
        if action != "pass" or flaky
        else None,
        trace=trace if action != "pass" or flaky else None,
        flaky=flaky,
        labels=[{"name": "subSuite", "value": test.name.rsplit("/", 1)[0]}]
        if "/" in test.name
        else [],
    )


def gotest_results_for_file(path: Path, *, tool: str) -> list[dict[str, Any]]:
    """Allure result dicts for one ``go test -json`` file; :class:`ValueError` if it isn't one."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not is_gotest_text(text):
        raise ValueError("not go test -json output (no Action/Package events)")
    return gotest_results_for_text(text, tool=tool, fallback_ms=int(path.stat().st_mtime * 1000))
