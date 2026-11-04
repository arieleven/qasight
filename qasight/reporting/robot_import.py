"""Convert Robot Framework's ``output.xml`` into Allure ``*-result.json`` dicts.

``robot`` writes ``output.xml`` on every run. It has more than its ``--xunit``
file: tags, the failure message of each test, and nested suites. Both the
Robot Framework 7 format (``start`` + ``elapsed``) and the older one
(``starttime`` / ``endtime``) are read.

Status mapping:

    PASS            -> passed
    FAIL            -> failed
    SKIP, NOT RUN   -> skipped

A failed suite teardown fails every test in that suite and below that had
passed (as ``robot`` reports it: "Parent suite teardown failed"); output.xml
keeps the tests' own PASS, so this is applied here.

``full_name`` is Robot's own long name (``Suite.Sub Suite.Test Name``), which
is what ``robot --test`` takes to rerun a test.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from qasight.reporting.imported_results import iso_ms, make_result

_STATUS = {"PASS": "passed", "FAIL": "failed", "SKIP": "skipped", "NOT RUN": "skipped"}


def is_robot_root(root: ET.Element) -> bool:
    return root.tag == "robot"


def _legacy_ms(value: str | None) -> int | None:
    if not value or value == "N/A":
        return None
    try:
        return int(datetime.strptime(value, "%Y%m%d %H:%M:%S.%f").timestamp() * 1000)
    except ValueError:
        return None


def _timing(status: ET.Element, fallback_ms: int) -> tuple[int, int]:
    if status.get("start"):
        start = iso_ms(status.get("start"), fallback_ms)
        try:
            elapsed = int(round(float(status.get("elapsed") or 0) * 1000))
        except (ValueError, OverflowError):
            elapsed = 0
        return start, elapsed
    legacy_start = _legacy_ms(status.get("starttime"))
    end = _legacy_ms(status.get("endtime"))
    if legacy_start is None:
        return fallback_ms, 0
    return legacy_start, max(0, (end or legacy_start) - legacy_start)


def robot_results_for_root(
    root: ET.Element, *, tool: str, fallback_ms: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def visit(suite: ET.Element, path: tuple[str, ...], teardown: str | None) -> None:
        names = (*path, suite.get("name") or "Suite")
        own = _failed_teardown(suite)
        if own is not None:
            teardown = own
        for test in suite.findall("test"):
            status_node = test.find("status")
            if status_node is None:
                continue
            name = test.get("name") or "(unnamed)"
            status = _STATUS.get((status_node.get("status") or "").upper(), "broken")
            start, elapsed = _timing(status_node, fallback_ms)
            message = (status_node.text or "").strip() or None
            if teardown is not None and status == "passed":
                status = "failed"
                message = f"Parent suite teardown failed:\n{teardown}".strip()
            tags = [t.text for t in test.findall("tag") if t.text]
            tags += [t.text for t in test.findall("tags/tag") if t.text]
            out.append(
                make_result(
                    tool=tool,
                    framework="robot",
                    language="robot",
                    name=name,
                    full_name=".".join((*names, name)),
                    status=status,
                    start_ms=start,
                    duration_ms=elapsed,
                    suite=".".join(names),
                    message=message if status != "passed" else None,
                    trace=message if status != "passed" else None,
                    labels=[{"name": "tag", "value": t} for t in dict.fromkeys(tags)],
                )
            )
        for child in suite.findall("suite"):
            visit(child, names, teardown)

    for suite in root.findall("suite"):
        visit(suite, (), None)
    return out


def _failed_teardown(suite: ET.Element) -> str | None:
    """The message of ``suite``'s own failed teardown keyword, else ``None``.

    Robot 7 writes ``<kw type="TEARDOWN">``; Robot 4-6 ``<kw type="teardown">``.
    """
    for kw in suite.findall("kw"):
        if (kw.get("type") or "").lower() != "teardown":
            continue
        status = kw.find("status")
        if status is not None and (status.get("status") or "").upper() == "FAIL":
            return (status.text or "").strip()
    return None


def robot_results_for_file(path: Path, *, tool: str) -> list[dict[str, Any]]:
    """Allure result dicts for one ``output.xml``; :class:`ValueError` if it isn't one."""
    root = ET.parse(path).getroot()  # noqa: S314 — stage-owned file, expat ignores external entities
    if not is_robot_root(root):
        raise ValueError("not a Robot Framework output.xml (no <robot> root)")
    return robot_results_for_root(root, tool=tool, fallback_ms=int(path.stat().st_mtime * 1000))
