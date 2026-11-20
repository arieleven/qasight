"""Rows of the ``test_result`` table: one test's outcome in one run.

The only code that reads or writes :class:`~qasight.repository.models.TestResult`.
Callers pass and receive the case map :func:`qasight.reporting.allure_cases.load_cases`
produces, so the services never see rows.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete
from sqlalchemy import select as sa_select
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from qasight.repository.models import TestResult

_WRITE_BATCH = 500

# One SQLite read of a run's rows at a time per process. The sqlite3 module gives up the
# GIL for every row it steps, so threads reading thousands of rows at once take turns on
# each row: eight concurrent reads of 20 runs x 5,000 rows took 156 s instead of 1.7 s
# one after another (docs/Specs & ADRs/Stress Test - 2026-11-20.md). Postgres and MySQL
# drivers fetch a result in one call, so they don't take it.
_SQLITE_READ_LOCK = threading.Lock()
_CASE_COLUMNS = (
    "key",
    "name",
    "full_name",
    "group",
    "status",
    "duration_ms",
    "message",
    "stage",
    "framework",
    "crash",
    "retried",
    "labels",
    "links",
)


def _pairs(value: Any) -> list[dict[str, str]] | None:
    """A case's ``labels`` / ``links`` list as stored: string-valued dicts, ``None`` when empty."""
    if not isinstance(value, list):
        return None
    out = [{str(k): str(v) for k, v in item.items()} for item in value if isinstance(item, dict)]
    return out or None


def _row(run_id: str, key: str, case: Mapping[str, Any]) -> TestResult:
    duration = case.get("duration_ms")
    return TestResult(
        run_id=run_id[:64],
        key=key[:200],
        name=str(case.get("name") or "")[:120],
        full_name=str(case.get("full_name") or "")[:1024],
        group=str(case.get("group") or "")[:100],
        stage=str(case.get("stage") or "")[:128],
        framework=str(case.get("framework") or "")[:32],
        status=str(case.get("status") or "unknown")[:16],
        duration_ms=int(duration) if isinstance(duration, int) else None,
        message=str(case.get("message") or "")[:300],
        crash=str(case.get("crash") or "")[:16],
        retried=bool(case.get("retried")),
        labels=_pairs(case.get("labels")),
        links=_pairs(case.get("links")),
    )


def _case(row: Any) -> dict[str, Any]:
    """A plain row of :data:`_CASE_COLUMNS` as the case map entry callers get."""
    (_, name, full_name, group, status, duration, message, stage, framework, crash) = row[:10]
    retried, labels, links = row[10:13]
    return {
        "name": name,
        "full_name": full_name,
        "group": group,
        "status": status,
        "duration_ms": duration,
        "message": message,
        "stage": stage,
        "framework": framework,
        "crash": crash,
        "retried": bool(retried),
        "labels": list(labels or []),
        "links": list(links or []),
    }


class TestResultStore:
    """Per-run test rows on a SQLAlchemy engine (the run-history DB by default)."""

    __test__ = False  # not a pytest test class

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def replace(self, run_id: str, cases: Mapping[str, Mapping[str, Any]]) -> int:
        """Store ``cases`` as ``run_id``'s rows, replacing any it had; returns the row count."""
        rows = [_row(run_id, key, case) for key, case in cases.items()]
        with Session(self._engine) as session:
            session.exec(delete(TestResult).where(col(TestResult.run_id) == run_id[:64]))  # type: ignore[call-overload]
            for start in range(0, len(rows), _WRITE_BATCH):
                session.add_all(rows[start : start + _WRITE_BATCH])
            session.commit()
        return len(rows)

    def cases(self, run_id: str) -> dict[str, dict[str, Any]]:
        """``run_id``'s tests as a case map; ``{}`` when none are stored.

        Plain rows rather than ORM objects: the flaky, failures and test history views
        read 20 runs of rows per request, and building model objects took most of it.
        """
        table = TestResult.__table__  # type: ignore[attr-defined]
        stmt = sa_select(*(table.c[name] for name in _CASE_COLUMNS)).where(
            table.c.run_id == run_id[:64]
        )
        if self._engine.dialect.name == "sqlite":
            with _SQLITE_READ_LOCK, self._engine.connect() as conn:
                rows = conn.execute(stmt).all()
        else:
            with self._engine.connect() as conn:
                rows = conn.execute(stmt).all()
        return {row[0]: _case(row) for row in rows}

    def has_rows(self, run_id: str) -> bool:
        stmt = select(TestResult.key).where(col(TestResult.run_id) == run_id[:64]).limit(1)
        with Session(self._engine) as session:
            return session.exec(stmt).first() is not None

    def delete_runs(self, run_ids: list[str]) -> int:
        """Drop every row of the given runs (retention); returns how many were deleted."""
        if not run_ids:
            return 0
        with Session(self._engine) as session:
            result = session.exec(  # type: ignore[call-overload]
                delete(TestResult).where(col(TestResult.run_id).in_([r[:64] for r in run_ids]))
            )
            session.commit()
            return int(result.rowcount or 0)
