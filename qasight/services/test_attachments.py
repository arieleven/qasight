"""A test's attachments in one run: Playwright traces, screenshots, videos and the like.

They are Allure attachments next to the test's ``*-result.json`` in the run's
snapshot (``static/history/<run_id>/artifacts/...``), written by the Allure
adapters or copied there by :mod:`qasight.reporting.playwright_import`.
The API serves that tree at ``/history``, so each attachment gets a URL
relative to the API root, like the run's report links
(:mod:`qasight.history.report_links`).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qasight import paths
from qasight.history.views import CompletedRunView
from qasight.services.report_archive_diff import _case_key

# What the test page can show inline, by MIME type prefix; anything else is a download link.
_KINDS = (
    ("image/", "image"),
    ("video/", "video"),
    ("application/zip", "trace"),
    ("text/", "text"),
)
MAX_ATTACHMENTS = 20


@dataclass(frozen=True)
class Attachment:
    name: str
    content_type: str
    # "image", "video", "trace" (a Playwright trace.zip), "text" or "file".
    kind: str
    # Relative to the API root, e.g. ``history/<run_id>/artifacts/.../<uuid>-attachment.png``.
    url: str
    size_bytes: int


def _kind(name: str, content_type: str) -> str:
    if content_type == "application/zip" and "trace" not in name.lower():
        return "file"
    return next((kind for prefix, kind in _KINDS if content_type.startswith(prefix)), "file")


def _raw_attachments(result: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """The test's own attachments, then each step's (Playwright keeps one step per attempt)."""
    yield from (a for a in result.get("attachments") or [] if isinstance(a, dict))
    for step in result.get("steps") or []:
        if isinstance(step, dict):
            yield from _raw_attachments(step)


def _result_for(snapshot: Path, key: str) -> Path | None:
    for path in snapshot.rglob("*-result.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and _case_key(data) == key:
            return path
    return None


def attachments_for(run: CompletedRunView, key: str) -> tuple[Attachment, ...]:
    """``key``'s attachments in ``run``, newest attempt first, files that exist only."""
    if not run.snapshot_dir:
        return ()
    history = paths.STATIC_HISTORY_ROOT.resolve()
    snapshot = (paths.ORCHESTRATOR_ROOT / run.snapshot_dir).resolve()
    if history not in snapshot.parents or not snapshot.is_dir():
        return ()
    result_path = _result_for(snapshot, key)
    if result_path is None:
        return ()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    found: dict[str, Attachment] = {}
    for raw in _raw_attachments(result):
        source = str(raw.get("source") or "")
        if not source or source in found or "/" in source or "\\" in source:
            continue
        file = result_path.parent / source
        if not file.is_file():
            continue
        name = str(raw.get("name") or source)[:120]
        content_type = str(raw.get("type") or "application/octet-stream")
        found[source] = Attachment(
            name=name,
            content_type=content_type,
            kind=_kind(name, content_type),
            url="history/" + file.resolve().relative_to(history).as_posix(),
            size_bytes=file.stat().st_size,
        )
    # Playwright puts the last attempt's attachments on the test itself, so they come first.
    return tuple(found.values())[:MAX_ATTACHMENTS]
