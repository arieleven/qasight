"""Persistence for :class:`~testo_core.repository.models.ReportArchive`."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from testo_core.repository.models import ReportArchive


class SQLReportArchiveRepository:
    """Insert/list/get report archives using the shared SQLAlchemy engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert(
        self,
        *,
        cycle_name: str,
        exit_code: int,
        summary_json: dict[str, Any],
        artifact_bytes: bytes,
        total_tests: int | None = None,
        passed: int | None = None,
        failed: int | None = None,
        broken: int | None = None,
        skipped: int | None = None,
        unknown: int | None = None,
        allure_duration_ms: int | None = None,
        plan_duration_ms: int | None = None,
    ) -> ReportArchive:
        row = ReportArchive(
            cycle_name=cycle_name,
            exit_code=int(exit_code),
            summary_json=dict(summary_json or {}),
            artifact_bytes=artifact_bytes,
            total_tests=total_tests,
            passed=passed,
            failed=failed,
            broken=broken,
            skipped=skipped,
            unknown=unknown,
            allure_duration_ms=allure_duration_ms,
            plan_duration_ms=plan_duration_ms,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
        return row

    def get(self, report_id: uuid.UUID | str) -> ReportArchive | None:
        try:
            rid = report_id if isinstance(report_id, uuid.UUID) else uuid.UUID(str(report_id))
        except (ValueError, TypeError):
            return None
        with Session(self._engine) as session:
            r = session.get(ReportArchive, rid)
            if r is None:
                return None
            session.expunge(r)
            return r

    def list_recent(self, *, limit: int = 30) -> list[ReportArchive]:
        stmt = (
            select(ReportArchive).order_by(col(ReportArchive.created_at).desc()).limit(int(limit))
        )
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
            for r in rows:
                session.expunge(r)
            return rows

    def list_recent_for_cycle(self, *, cycle_name: str, limit: int = 10) -> list[ReportArchive]:
        stmt = (
            select(ReportArchive)
            .where(ReportArchive.cycle_name == cycle_name)
            .order_by(col(ReportArchive.created_at).desc())
            .limit(int(limit))
        )
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
            for r in rows:
                session.expunge(r)
            return rows

    def list_keys(self) -> list[tuple[str, str, datetime]]:
        """``(id, cycle_name, created_at)`` of every archive, newest first, without the blobs."""
        stmt = select(
            ReportArchive.id, ReportArchive.cycle_name, ReportArchive.created_at
        ).order_by(col(ReportArchive.created_at).desc())
        with Session(self._engine) as session:
            return [(str(rid), cycle, created) for rid, cycle, created in session.exec(stmt).all()]

    def delete(self, report_ids: list[str]) -> int:
        """Delete these archives; return how many went. Ids that aren't archives are skipped."""
        ids: list[uuid.UUID] = []
        for report_id in report_ids:
            try:
                ids.append(uuid.UUID(str(report_id)))
            except ValueError:
                continue
        deleted = 0
        with Session(self._engine) as session:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                result = session.exec(  # type: ignore[call-overload]
                    sa_delete(ReportArchive).where(col(ReportArchive.id).in_(chunk))
                )
                deleted += int(result.rowcount or 0)
            session.commit()
        return deleted
