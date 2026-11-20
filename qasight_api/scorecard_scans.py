"""Scorecard scans started from the UI: one at a time, in a background thread.

The scan itself is :func:`qasight.services.scorecard.scan.run_scan`, the same as
``qasight scorecard scan``. This only tracks its progress for the page to poll.

The worker that starts a scan runs it and writes each step to the ``scorecard_scan``
table, so with ``TESTO_WORKERS`` above 1 any worker answers the page's polls, and
"one at a time" holds across workers. A scan whose row stops changing for
:data:`STALE_AFTER_S` (its worker died) no longer blocks a new one.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_KEPT = 20
STALE_AFTER_S = 2 * 3600


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

    def data(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "project": self.project,
            "version": self.version,
            "languages": list(self.languages),
            "message": self.message,
            "tools": {tool: dict(info) for tool, info in self.tools.items()},
            "skipped": dict(self.skipped),
        }

    @classmethod
    def from_row(cls, row: Any) -> ScanState:
        data = dict(row.data or {})
        return cls(
            id=row.id,
            target=str(data.get("target") or ""),
            state=row.state,
            project=data.get("project"),
            version=data.get("version"),
            languages=list(data.get("languages") or []),
            message=str(data.get("message") or ""),
            tools=dict(data.get("tools") or {}),
            skipped=dict(data.get("skipped") or {}),
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

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
    def __init__(self, engine: Any = None) -> None:
        self._lock = threading.Lock()
        self._engine = engine
        self._ready: set[tuple[int, str]] = set()

    def _db(self) -> Any:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        engine = self._engine if self._engine is not None else get_engine()
        key = (id(engine), str(engine.url))
        if key not in self._ready:
            create_db_and_tables(engine)
            self._ready.add(key)
        return engine

    def _save(self, state: ScanState) -> None:
        """Write *state* for the other workers; a failed write never stops the scan."""
        from sqlmodel import Session

        from qasight.repository.models import ScorecardScanRow

        with self._lock:
            row = ScorecardScanRow(
                id=state.id,
                state=state.state,
                started_at=state.started_at,
                updated_at=time.time(),
                finished_at=state.finished_at,
                data=state.data(),
            )
        try:
            with Session(self._db()) as db:
                db.merge(row)
                db.commit()
        except Exception:  # noqa: BLE001
            logger.warning("could not save scorecard scan %s", state.id, exc_info=True)

    def get(self, scan_id: str) -> ScanState | None:
        from sqlmodel import Session

        from qasight.repository.models import ScorecardScanRow

        with Session(self._db()) as db:
            row = db.get(ScorecardScanRow, scan_id)
            return ScanState.from_row(row) if row is not None else None

    def start(self, request: Any) -> ScanState:
        from sqlmodel import Session, col, select

        from qasight.repository.models import ScorecardScanRow
        from qasight.services.scorecard.scan import checked_diff_base, known_tool_ids

        only = known_tool_ids(",".join(request.tools)) if request.tools else None
        checked_diff_base(request.diff_base)
        state = ScanState(
            id=uuid.uuid4().hex[:12], target=request.target.strip(), message="Starting"
        )
        now = time.time()
        with self._lock, Session(self._db()) as db:
            running = db.exec(
                select(ScorecardScanRow).where(ScorecardScanRow.state == "running")
            ).all()
            for row in running:
                if row.updated_at >= now - STALE_AFTER_S:
                    raise ScanBusyError("A scan is already running. Wait for it to finish.")
                row.state, row.finished_at = "failed", now
                row.data = {**(row.data or {}), "message": "The server stopped during the scan"}
                db.add(row)
            db.add(
                ScorecardScanRow(
                    id=state.id,
                    state=state.state,
                    started_at=state.started_at,
                    updated_at=now,
                    data=state.data(),
                )
            )
            db.commit()
            ids = db.exec(
                select(ScorecardScanRow.id).order_by(col(ScorecardScanRow.started_at).desc())
            ).all()
            for old in ids[MAX_KEPT:]:
                stale = db.get(ScorecardScanRow, old)
                if stale is not None:
                    db.delete(stale)
            db.commit()
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
        self._save(state)

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
            self._save(state)
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
                diff_base=request.diff_base or None,
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
            self._save(state)


_MANAGER = ScorecardScanManager()


def get_scan_manager() -> ScorecardScanManager:
    return _MANAGER
