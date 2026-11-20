"""Rows of the ``test_result`` table: one test's outcome in one run.

The only code that reads or writes :class:`~qasight.repository.models.TestResult`.
Callers pass and receive the case map :func:`qasight.reporting.allure_cases.load_cases`
produces, so the services never see rows.
"""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import String, delete, type_coerce
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
_IN_CHUNK = 400  # bound parameters per IN list (SQLite allows 999 on old builds)

# Finished runs' rows, kept in memory: a run's tests don't change once it is recorded, and
# the flaky, failures and test search views read the same 20 runs on every request. The
# budget is in rows (about 1 KB each); TESTO_CASE_CACHE_ROWS changes it, 0 turns it off.
DEFAULT_CACHE_ROWS = 100_000
_cache_lock = threading.Lock()
_cache: OrderedDict[tuple[str, str], dict[str, dict[str, Any]]] = OrderedDict()
_cache_rows = 0


def cache_budget() -> int:
    """Rows the in-memory cache of runs' tests may hold (``TESTO_CASE_CACHE_ROWS``)."""
    raw = os.environ.get("TESTO_CASE_CACHE_ROWS", "").strip()
    if not raw:
        return DEFAULT_CACHE_ROWS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_CACHE_ROWS


def clear_cache() -> None:
    """Forget every cached run (tests, and after a restore from backup)."""
    global _cache_rows
    with _cache_lock:
        _cache.clear()
        _cache_rows = 0


def _cache_get(key: tuple[str, str]) -> dict[str, dict[str, Any]] | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
        return hit


def _cache_put(key: tuple[str, str], cases: dict[str, dict[str, Any]]) -> None:
    global _cache_rows
    budget = cache_budget()
    if not cases or len(cases) > budget:
        return
    with _cache_lock:
        old = _cache.pop(key, None)
        if old is not None:
            _cache_rows -= len(old)
        _cache[key] = cases
        _cache_rows += len(cases)
        while _cache_rows > budget and _cache:
            _, evicted = _cache.popitem(last=False)
            _cache_rows -= len(evicted)


def _cache_drop(keys: Iterable[tuple[str, str]]) -> None:
    global _cache_rows
    with _cache_lock:
        for key in keys:
            old = _cache.pop(key, None)
            if old is not None:
                _cache_rows -= len(old)


def _json_list(value: Any) -> list[Any]:
    """A ``labels`` / ``links`` value read as text: most rows hold none, so skip the parser."""
    if value is None:
        return []
    if isinstance(value, str):
        if value in ("null", "[]", ""):
            return []
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return list(value) if isinstance(value, list) else []


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
        "labels": _json_list(labels),
        "links": _json_list(links),
    }


class TestResultStore:
    """Per-run test rows on a SQLAlchemy engine (the run-history DB by default)."""

    __test__ = False  # not a pytest test class

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def replace(self, run_id: str, cases: Mapping[str, Mapping[str, Any]]) -> int:
        """Store ``cases`` as ``run_id``'s rows, replacing any it had; returns the row count."""
        rows = [_row(run_id, key, case) for key, case in cases.items()]
        _cache_drop([self._key(run_id)])
        with Session(self._engine) as session:
            session.exec(delete(TestResult).where(col(TestResult.run_id) == run_id[:64]))  # type: ignore[call-overload]
            for start in range(0, len(rows), _WRITE_BATCH):
                session.add_all(rows[start : start + _WRITE_BATCH])
            session.commit()
        return len(rows)

    def _key(self, run_id: str) -> tuple[str, str]:
        return (str(self._engine.url), run_id[:64])

    def _select(self, stmt: Any) -> list[Any]:
        if self._engine.dialect.name == "sqlite":
            with _SQLITE_READ_LOCK, self._engine.connect() as conn:
                return list(conn.execute(stmt).all())
        with self._engine.connect() as conn:
            return list(conn.execute(stmt).all())

    @staticmethod
    def _columns() -> list[Any]:
        table = TestResult.__table__  # type: ignore[attr-defined]
        # labels and links come back as text and are parsed only when a row has some.
        return [
            type_coerce(table.c[name], String) if name in ("labels", "links") else table.c[name]
            for name in _CASE_COLUMNS
        ]

    def cases(self, run_id: str) -> dict[str, dict[str, Any]]:
        """``run_id``'s tests as a case map; ``{}`` when none are stored.

        Plain rows rather than ORM objects, kept in memory after the first read (see
        :data:`DEFAULT_CACHE_ROWS`). The map is the caller's to change; the cases in it
        are shared, so treat them as read-only.
        """
        key = self._key(run_id)
        hit = _cache_get(key)
        if hit is not None:
            return dict(hit)
        table = TestResult.__table__  # type: ignore[attr-defined]
        stmt = sa_select(*self._columns()).where(table.c.run_id == run_id[:64])
        cases = {row[0]: _case(row) for row in self._select(stmt)}
        _cache_put(key, cases)
        return dict(cases)

    def cases_for_keys(
        self, run_ids: list[str], keys: Iterable[str]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """The given tests' rows in the given runs, as ``{run_id: {key: case}}``.

        For views that follow a few tests back through history (a run's failures): one
        query on the primary key instead of every row of every run. A run with none of
        the tests is left out, so callers that need to know a run has rows at all ask
        :meth:`runs_with_rows`.
        """
        wanted = sorted({k[:200] for k in keys})
        ids = [r[:64] for r in run_ids]
        out: dict[str, dict[str, dict[str, Any]]] = {}
        missing: list[str] = []
        for run_id in ids:
            hit = _cache_get(self._key(run_id))
            if hit is None:
                missing.append(run_id)
                continue
            found = {k: hit[k] for k in wanted if k in hit}
            if found:
                out[run_id] = found
        if not missing or not wanted:
            return out
        table = TestResult.__table__  # type: ignore[attr-defined]
        columns = [table.c.run_id, *self._columns()]
        for start in range(0, len(wanted), _IN_CHUNK):
            chunk = wanted[start : start + _IN_CHUNK]
            stmt = sa_select(*columns).where(table.c.run_id.in_(missing), table.c.key.in_(chunk))
            for row in self._select(stmt):
                out.setdefault(row[0], {})[row[1]] = _case(row[1:])
        return out

    def runs_with_rows(self, run_ids: list[str]) -> set[str]:
        """Which of ``run_ids`` have any rows stored."""
        ids = [r[:64] for r in run_ids]
        have = {r for r in ids if _cache_get(self._key(r)) is not None}
        table = TestResult.__table__  # type: ignore[attr-defined]
        for run_id in (r for r in ids if r not in have):
            stmt = sa_select(table.c.key).where(table.c.run_id == run_id).limit(1)
            if self._select(stmt):
                have.add(run_id)
        return have

    def has_rows(self, run_id: str) -> bool:
        stmt = select(TestResult.key).where(col(TestResult.run_id) == run_id[:64]).limit(1)
        with Session(self._engine) as session:
            return session.exec(stmt).first() is not None

    def delete_runs(self, run_ids: list[str]) -> int:
        """Drop every row of the given runs (retention); returns how many were deleted."""
        if not run_ids:
            return 0
        _cache_drop(self._key(r) for r in run_ids)
        with Session(self._engine) as session:
            result = session.exec(  # type: ignore[call-overload]
                delete(TestResult).where(col(TestResult.run_id).in_([r[:64] for r in run_ids]))
            )
            session.commit()
            return int(result.rowcount or 0)
