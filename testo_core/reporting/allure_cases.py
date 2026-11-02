"""One run's tests as a flat map, read from the Allure ``*-result.json`` files of an artifact tree.

This is the shape the analytics share (``flaky``, per-test history, failures,
Compare, the PR comment): ``{key: {name, full_name, group, status, duration_ms,
message, stage, framework, crash, retried, labels, links}}``. Since revision
``0003`` the same rows are also stored in the ``test_result`` table when a run is recorded
(:mod:`testo_core.repository.test_results`); this module is the parser that
fills it, and the fallback for snapshots recorded before the table existed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from testo_core.engine.crash_signatures import crash_in_text

# Bounds for the stored columns; the files keep the full text.
NAME_LIMIT = 120
GROUP_LIMIT = 100
MESSAGE_LIMIT = 300
KEY_LIMIT = 200
LABEL_VALUE_LIMIT = 100
LABELS_PER_TEST = 20
LINKS_PER_TEST = 10
URL_LIMIT = 500

# Allure labels that describe where a test lives or ran, not what it is about.
# Everything else (feature, story, epic, severity, owner, tag, layer, custom ones)
# is kept as one of the test's labels.
_STRUCTURAL_LABELS = frozenset(
    {
        "package",
        "testclass",
        "testmethod",
        "suite",
        "parentsuite",
        "subsuite",
        "host",
        "thread",
        "framework",
        "language",
        "as_id",
        "allure_id",
        "resultformat",
    }
)


def case_key(data: dict[str, Any]) -> str:
    """The id a test keeps across runs: Allure's ``historyId``, else its full name."""
    for k in ("historyId", "fullName", "uuid"):
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    name = data.get("name")
    return str(name) if name is not None else json.dumps(data, sort_keys=True, default=str)[:200]


def case_duration_ms(data: dict[str, Any]) -> int | None:
    start, stop = data.get("start"), data.get("stop")
    if isinstance(start, int) and isinstance(stop, int) and stop >= start:
        return int(stop - start)
    return None


def label_value(labels: Any, name: str) -> str:
    if not isinstance(labels, list):
        return ""
    for lab in labels:
        if isinstance(lab, dict) and str(lab.get("name")) == name:
            v = lab.get("value")
            return str(v).strip() if v is not None else ""
    return ""


def case_labels(data: dict[str, Any]) -> list[dict[str, str]]:
    """What the test is about: its feature, story, severity, owner and tags, as ``{name, value}``.

    Allure labels that only say where the test lives (package, suite, host,
    framework...) are left out; duplicates are dropped and the list is bounded.
    """
    labels = data.get("labels")
    if not isinstance(labels, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for lab in labels:
        if not isinstance(lab, dict):
            continue
        name = str(lab.get("name") or "").strip().lower()
        value = str(lab.get("value") or "").strip()
        if not name or not value or name in _STRUCTURAL_LABELS:
            continue
        pair = (name[:40], value[:LABEL_VALUE_LIMIT])
        if pair in seen:
            continue
        seen.add(pair)
        out.append({"name": pair[0], "value": pair[1]})
        if len(out) >= LABELS_PER_TEST:
            break
    return out


def case_links(data: dict[str, Any]) -> list[dict[str, str]]:
    """The test's links (issues, stories, docs) as ``{name, url, type}``; only http(s) URLs."""
    links = data.get("links")
    if not isinstance(links, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in links:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        kind = str(link.get("type") or "link").strip().lower() or "link"
        name = str(link.get("name") or "").strip() or url
        out.append({"name": name[:LABEL_VALUE_LIMIT], "url": url[:URL_LIMIT], "type": kind[:20]})
        if len(out) >= LINKS_PER_TEST:
            break
    return out


def case_message(data: dict[str, Any]) -> str:
    """First non-empty line of the failure message (or trace), for lists and grouping."""
    details = data.get("statusDetails")
    if not isinstance(details, dict):
        return ""
    text = str(details.get("message") or details.get("trace") or "")
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line[:MESSAGE_LIMIT]


def case_crash(data: dict[str, Any], status: str) -> str:
    """``"stack_overflow"`` / ``"crash"`` when a failed case's message or trace shows one, else ``""``."""
    if status not in ("failed", "broken"):
        return ""
    details = data.get("statusDetails")
    if not isinstance(details, dict):
        return ""
    text = f"{details.get('message') or ''}\n{details.get('trace') or ''}"
    found = crash_in_text(text)
    return found.kind if found else ""


def case_stage(path: Path, plan_root: Path) -> str:
    """The stage a result file belongs to: the folder just above ``allure-results``."""
    parts = path.relative_to(plan_root).parts
    if "allure-results" in parts:
        idx = parts.index("allure-results")
        if idx > 0:
            return parts[idx - 1]
    return ""


def case_group(data: dict[str, Any]) -> str:
    """Stable grouping key (module / suite) for dashboard tables."""
    labels = data.get("labels") or []
    pkg = label_value(labels, "package")
    if pkg:
        return pkg[:GROUP_LIMIT]
    ps, su = label_value(labels, "parentSuite"), label_value(labels, "suite")
    if ps and su:
        return f"{ps} › {su}"[:GROUP_LIMIT]
    if ps:
        return ps[:GROUP_LIMIT]
    fn = str(data.get("fullName") or data.get("name") or "")
    if "#" in fn:
        return fn.split("#", 1)[0][:GROUP_LIMIT]
    parts = fn.rsplit(".", 1)
    if len(parts) == 2 and parts[0] and "." in parts[0]:
        return parts[0][:GROUP_LIMIT]
    return "(ungrouped)"


def load_cases(plan_root: Path) -> dict[str, dict[str, Any]]:
    """Every ``*-result.json`` under ``plan_root`` as ``{key: case}``; ``{}`` when there is none."""
    out: dict[str, dict[str, Any]] = {}
    if not plan_root.is_dir():
        return out
    # Thousands of results share a folder; working out its stage once per folder
    # instead of once per file was most of the dashboard's load time.
    stages: dict[Path, str] = {}
    for path in plan_root.rglob("*-result.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        key = case_key(data)
        details = data.get("statusDetails")
        st = str(data.get("status") or "unknown").lower()
        stage = stages.get(path.parent)
        if stage is None:
            stage = stages[path.parent] = case_stage(path, plan_root)
        out[key] = {
            "name": str(data.get("name") or data.get("fullName") or key)[:NAME_LIMIT],
            "full_name": str(data.get("fullName") or ""),
            "group": case_group(data),
            "status": st,
            "duration_ms": case_duration_ms(data),
            "message": case_message(data),
            "stage": stage,
            "framework": label_value(data.get("labels"), "framework"),
            "crash": case_crash(data, st),
            # Failed, then passed on a retry inside this run (JUnit reruns, Surefire).
            "retried": isinstance(details, dict) and bool(details.get("flaky")),
            "labels": case_labels(data),
            "links": case_links(data),
        }
    return out
