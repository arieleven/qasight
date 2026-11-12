"""Scorecard scans started from the UI: one at a time, in a background thread, kept in memory.

The scan itself is :func:`qasight.services.scorecard.scan.run_scan`, the same as
``qasight scorecard scan``. This only tracks its progress for the page to poll.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_KEPT = 20


class ScanBusyError(RuntimeError):
    """Another scan is still running."""


@dataclass
class ScanState:
    id: str
    target: str
    state: str = "running"
    project: str | None = None
    version: str | None = None
    languages: list[str] = field(default_factory=list)
    message: str = ""
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "state": self.state,
            "project": self.project,
            "version": self.version,
            "languages": list(self.languages),
            "message": self.message,
            "tools": [{"tool": tool, **info} for tool, info in self.tools.items()],
            "skipped": dict(self.skipped),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class ScorecardScanManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scans: dict[str, ScanState] = {}

    def get(self, scan_id: str) -> ScanState | None:
        with self._lock:
            return self._scans.get(scan_id)

    def start(self, request: Any) -> ScanState:
        from qasight.services.scorecard.scan import known_tool_ids

        only = known_tool_ids(",".join(request.tools)) if request.tools else None
        with self._lock:
            if any(s.state == "running" for s in self._scans.values()):
                raise ScanBusyError("A scan is already running. Wait for it to finish.")
            state = ScanState(
                id=uuid.uuid4().hex[:12], target=request.target.strip(), message="Starting"
            )
            self._scans[state.id] = state
            for old in sorted(self._scans.values(), key=lambda s: s.started_at)[:-MAX_KEPT]:
                self._scans.pop(old.id, None)
        thread = threading.Thread(
            target=self._run,
            args=(state, request, only),
            name=f"scorecard-scan-{state.id}",
            daemon=True,
        )
        thread.start()
        return state

    def _event(self, state: ScanState, event: dict[str, Any]) -> None:
        with self._lock:
            if event["type"] == "clone":
                state.message = event["message"]
            elif event["type"] == "plan":
                state.languages = list(event["languages"])
                state.skipped = dict(event["skipped"])
                state.tools = {
                    tool: {"status": "queued", "score": None, "message": ""}
                    for tool in event["tools"]
                }
                state.message = f"Running {len(event['tools'])} tool(s)"
            elif event["type"] == "tool":
                if event["status"] == "skipped":
                    state.tools.pop(event["tool"], None)
                    state.skipped[event["tool"]] = event.get("message", "")
                else:
                    state.tools[event["tool"]] = {
                        "status": event["status"],
                        "score": event.get("score"),
                        "message": event.get("message", ""),
                    }

    def _run(self, state: ScanState, request: Any, only: list[str] | None) -> None:
        from qasight.services.scorecard.scan import finish, resolve_target, run_scan
        from qasight.services.scorecard.store import ScorecardStore

        out_dir = Path(tempfile.mkdtemp(prefix="qasight-scorecard-"))
        target = None
        try:
            target = resolve_target(
                request.target,
                project=request.project,
                version=request.version,
                ref=request.ref,
                on_event=lambda e: self._event(state, e),
            )
            with self._lock:
                state.project, state.version = target.project, target.version
            store = ScorecardStore()

            def record(tool: str, result: Any) -> None:
                store.record(
                    project=target.project,
                    version=target.version,
                    tool=tool,
                    result=result,
                    commit_sha=target.commit_sha,
                    source="scan",
                )

            outcome = run_scan(
                target,
                out_dir=out_dir,
                enabled=store.enabled(),
                record=record,
                only=only,
                url=request.url or None,
                openapi=request.openapi or None,
                on_event=lambda e: self._event(state, e),
                record_coverage=lambda o: store.record_coverage(
                    project=target.project,
                    version=target.version,
                    expected=o.expected,
                    missing=o.missing(),
                    languages=sorted(o.languages),
                ),
            )
            with self._lock:
                state.state = "done" if outcome.results else "failed"
                state.message = (
                    f"{len(outcome.results)} tool(s) scored"
                    if outcome.results
                    else "No tool produced a result. Install one of the skipped tools on this machine."
                )
        except Exception as exc:  # noqa: BLE001 - the page shows why the scan stopped
            with self._lock:
                state.state = "failed"
                state.message = str(exc)
        finally:
            if target is not None:
                finish(target)
            shutil.rmtree(out_dir, ignore_errors=True)
            with self._lock:
                state.finished_at = time.time()


_MANAGER = ScorecardScanManager()


def get_scan_manager() -> ScorecardScanManager:
    return _MANAGER
