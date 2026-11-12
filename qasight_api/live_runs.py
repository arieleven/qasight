"""Test sessions that are still running, reported test by test (``pytest --qasight-server``).

A recorded run only exists once the session ends and uploads its report. While it runs,
the pytest plugin sends each result here so the dashboard shows progress and the first
failures right away. Kept in memory: a live session is minutes long, the server runs
one process, and the finished session is recorded as a normal run by the upload.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

MAX_FAILURES = 50
STALE_AFTER_S = 15 * 60
FINISHED_KEEP_S = 2 * 60
MAX_SESSIONS = 200

OUTCOMES = ("passed", "failed", "skipped", "error")


@dataclass
class LiveFailure:
    test: str
    message: str


@dataclass
class LiveSession:
    id: str
    cycle: str
    started_at: float
    total: int | None = None
    branch: str | None = None
    commit: str | None = None
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))
    failures: list[LiveFailure] = field(default_factory=list)
    current: str | None = None
    updated_at: float = 0.0
    finished_at: float | None = None

    @property
    def done(self) -> int:
        return sum(self.counts.values())


class LiveRunStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, LiveSession] = {}

    def start(
        self,
        cycle: str,
        *,
        total: int | None = None,
        branch: str | None = None,
        commit: str | None = None,
        now: float | None = None,
    ) -> LiveSession:
        at = time.time() if now is None else now
        session = LiveSession(
            id=secrets.token_urlsafe(12),
            cycle=cycle,
            started_at=at,
            total=total,
            branch=branch,
            commit=commit,
            updated_at=at,
        )
        with self._lock:
            self._prune(at)
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions.values(), key=lambda s: s.updated_at)
                del self._sessions[oldest.id]
            self._sessions[session.id] = session
        return session

    def add_results(
        self,
        session_id: str,
        results: Sequence[tuple[str, str, str | None]],
        *,
        now: float | None = None,
    ) -> LiveSession | None:
        """Add ``(test, outcome, message)`` results; None when the session is unknown."""
        at = time.time() if now is None else now
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.finished_at is not None:
                return session
            for test, outcome, message in results:
                session.counts[outcome] = session.counts.get(outcome, 0) + 1
                session.current = test
                if outcome in ("failed", "error") and len(session.failures) < MAX_FAILURES:
                    session.failures.append(LiveFailure(test=test, message=(message or "")[:500]))
            session.updated_at = at
            return session

    def finish(self, session_id: str, *, now: float | None = None) -> LiveSession | None:
        at = time.time() if now is None else now
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and session.finished_at is None:
                session.finished_at = at
                session.current = None
                session.updated_at = at
            return session

    def active(self, *, now: float | None = None) -> list[LiveSession]:
        """Running sessions and ones that finished in the last two minutes, newest first."""
        at = time.time() if now is None else now
        with self._lock:
            self._prune(at)
            return sorted(self._sessions.values(), key=lambda s: s.started_at, reverse=True)

    def _prune(self, now: float) -> None:
        for sid, session in list(self._sessions.items()):
            if session.finished_at is not None:
                if now - session.finished_at > FINISHED_KEEP_S:
                    del self._sessions[sid]
            elif now - session.updated_at > STALE_AFTER_S:
                del self._sessions[sid]

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


live_runs = LiveRunStore()
