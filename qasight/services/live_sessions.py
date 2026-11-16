"""Test sessions that are still running, reported test by test.

A recorded run only exists once the session ends and uploads its report. While it runs,
``pytest --qasight-server`` (and a ReportPortal agent pointed at the compatible endpoint,
:mod:`qasight.services.rp_compat`) sends each result here, so the dashboard shows progress
and the first failures right away.

Sessions live in the run-history database (table ``live_session``), not in one
process's memory: a server started with several workers, or several replicas behind a
load balancer, shows the same "Running now" list whichever one a request reaches. A
session is minutes long; finished ones are dropped two minutes after they end and silent
ones after fifteen, by whichever request next lists or starts one.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, or_
from sqlmodel import Session, col, select

from qasight.repository.models import LiveSessionRow

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
    source: str = "pytest"

    @property
    def done(self) -> int:
        return sum(self.counts.values())


def _session(row: LiveSessionRow) -> LiveSession:
    counts = dict.fromkeys(OUTCOMES, 0)
    counts.update({k: int(v) for k, v in (row.counts or {}).items()})
    return LiveSession(
        id=row.id,
        cycle=row.cycle,
        started_at=row.started_at,
        total=row.total,
        branch=row.branch,
        commit=row.commit,
        counts=counts,
        failures=[
            LiveFailure(test=str(f.get("test", "")), message=str(f.get("message", "")))
            for f in row.failures or []
        ],
        current=row.current,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
        source=row.source,
    )


class LiveRunStore:
    """Live sessions on a SQLAlchemy engine (the run-history database by default)."""

    def __init__(self, engine: Any = None) -> None:
        self._engine = engine
        self._ready: set[tuple[int, str]] = set()

    def _db(self) -> Any:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        # Looked up on every call, so a changed DATABASE_URL (tests, ``reset_engine_cache``)
        # is followed like every other store does.
        engine = self._engine if self._engine is not None else get_engine()
        key = (id(engine), str(engine.url))
        if key not in self._ready:
            create_db_and_tables(engine)
            self._ready.add(key)
        return engine

    def start(
        self,
        cycle: str,
        *,
        total: int | None = None,
        branch: str | None = None,
        commit: str | None = None,
        source: str = "pytest",
        now: float | None = None,
    ) -> LiveSession:
        at = time.time() if now is None else now
        row = LiveSessionRow(
            id=secrets.token_urlsafe(12),
            cycle=cycle[:512],
            source=source[:32],
            started_at=at,
            updated_at=at,
            total=total,
            branch=(branch or None) and branch[:200],
            commit=(commit or None) and commit[:64],
            counts=dict.fromkeys(OUTCOMES, 0),
            failures=[],
        )
        with Session(self._db()) as db:
            self._prune(db, at)
            count = len(db.exec(select(LiveSessionRow.id)).all())
            if count >= MAX_SESSIONS:
                oldest = db.exec(
                    select(LiveSessionRow).order_by(LiveSessionRow.updated_at).limit(1)  # type: ignore[arg-type]
                ).first()
                if oldest is not None:
                    db.delete(oldest)
            db.add(row)
            db.commit()
            db.refresh(row)
            return _session(row)

    def add_results(
        self,
        session_id: str,
        results: Sequence[tuple[str, str, str | None]],
        *,
        now: float | None = None,
    ) -> LiveSession | None:
        """Add ``(test, outcome, message)`` results; None when the session is unknown."""
        at = time.time() if now is None else now
        with Session(self._db()) as db:
            # FOR UPDATE on PostgreSQL and MySQL, so two workers adding results to one
            # session don't lose each other's counts; SQLite serialises writes anyway.
            row = db.exec(
                select(LiveSessionRow).where(LiveSessionRow.id == session_id).with_for_update()
            ).first()
            if row is None or row.finished_at is not None:
                return _session(row) if row is not None else None
            counts = dict(row.counts or {})
            failures = list(row.failures or [])
            for test, outcome, message in results:
                counts[outcome] = int(counts.get(outcome, 0)) + 1
                row.current = test[:1000]
                if outcome in ("failed", "error") and len(failures) < MAX_FAILURES:
                    failures.append({"test": test[:1000], "message": (message or "")[:500]})
            row.counts = counts
            row.failures = failures
            row.updated_at = at
            db.add(row)
            db.commit()
            db.refresh(row)
            return _session(row)

    def set_total(self, session_id: str, total: int) -> None:
        with Session(self._db()) as db:
            row = db.get(LiveSessionRow, session_id)
            if row is not None:
                row.total = total
                db.add(row)
                db.commit()

    def finish(self, session_id: str, *, now: float | None = None) -> LiveSession | None:
        at = time.time() if now is None else now
        with Session(self._db()) as db:
            row = db.get(LiveSessionRow, session_id)
            if row is None:
                return None
            if row.finished_at is None:
                row.finished_at = at
                row.current = None
                row.updated_at = at
                db.add(row)
                db.commit()
                db.refresh(row)
            return _session(row)

    def active(self, *, now: float | None = None) -> list[LiveSession]:
        """Running sessions and ones that finished in the last two minutes, newest first."""
        at = time.time() if now is None else now
        with Session(self._db()) as db:
            self._prune(db, at)
            db.commit()
            rows = db.exec(
                select(LiveSessionRow).order_by(LiveSessionRow.started_at.desc())  # type: ignore[attr-defined]
            ).all()
            return [_session(r) for r in rows]

    @staticmethod
    def _prune(db: Session, now: float) -> None:
        db.execute(
            delete(LiveSessionRow).where(
                or_(
                    col(LiveSessionRow.finished_at) < now - FINISHED_KEEP_S,
                    col(LiveSessionRow.finished_at).is_(None)
                    & (col(LiveSessionRow.updated_at) < now - STALE_AFTER_S),
                )
            )
        )

    def clear(self) -> None:
        with Session(self._db()) as db:
            db.execute(delete(LiveSessionRow))
            db.commit()


live_runs = LiveRunStore()
