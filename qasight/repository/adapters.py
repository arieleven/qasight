"""SQLModel/SQLAlchemy repository implementation (multi-dialect via engine URL)."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .base import BaseRunRepository
from .models import RunRecord, RunStatus, sync_promoted_columns


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _run_uuid_from_external(run_id: str | uuid.UUID) -> uuid.UUID:
    """Convert external run id to a stable UUID (reuse valid UUIDs; else uuid5)."""
    if isinstance(run_id, uuid.UUID):
        return run_id
    try:
        return uuid.UUID(str(run_id))
    except (ValueError, TypeError):
        return uuid.uuid5(uuid.NAMESPACE_URL, f"uqo-run:{run_id}")


class SQLModelRunRepository(BaseRunRepository):
    """SQLModel-backed repository; works with SQLite, PostgreSQL, and MySQL engines."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_run(
        self,
        *,
        status: RunStatus = RunStatus.PENDING,
        metadata: dict[str, Any] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> RunRecord:
        rr = RunRecord(
            status=status, start_time=start_time or _utcnow(), end_time=end_time, metadata_={}
        )
        base_md: dict[str, Any] = {"run_id": str(rr.id), "created_at": float(time.time())}
        if metadata:
            base_md.update(metadata)
        rr.metadata_ = base_md
        sync_promoted_columns(rr)
        with Session(self._engine) as session:
            session.add(rr)
            session.commit()
            session.refresh(rr)
            session.expunge(rr)
        return rr

    def get_run(self, run_id: uuid.UUID | str) -> RunRecord | None:
        rid = _run_uuid_from_external(run_id)
        with Session(self._engine) as session:
            r = session.get(RunRecord, rid)
            if r is None:
                return None
            session.expunge(r)
            return r

    def update_run_status(
        self,
        run_id: uuid.UUID | str,
        *,
        status: RunStatus,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        rid = _run_uuid_from_external(run_id)
        now = _utcnow()
        with Session(self._engine) as session:
            existing = session.get(RunRecord, rid)
            if existing is None:
                existing = RunRecord(
                    id=rid,
                    status=status,
                    start_time=now,
                    end_time=now if status in {RunStatus.COMPLETED, RunStatus.FAILED} else None,
                    metadata_=(metadata or {}),
                )
                sync_promoted_columns(existing)
                session.add(existing)
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing

            existing.status = status
            if existing.start_time is None:
                existing.start_time = now
            if status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                existing.end_time = now
            if metadata is not None:
                merged = dict(existing.metadata_ or {})
                merged.update(metadata)
                existing.metadata_ = merged
            sync_promoted_columns(existing)
            session.add(existing)
            session.commit()
            session.refresh(existing)
            session.expunge(existing)
            return existing

    def merge_run_metadata(self, run_id: uuid.UUID | str, patch: dict[str, Any]) -> bool:
        with Session(self._engine) as session:
            existing = session.get(RunRecord, _run_uuid_from_external(run_id))
            if existing is None:
                return False
            existing.metadata_ = {**(existing.metadata_ or {}), **patch}
            sync_promoted_columns(existing)
            session.add(existing)
            session.commit()
            return True

    def list_recent_runs(self, *, limit: int = 30) -> list[RunRecord]:
        stmt = select(RunRecord).order_by(col(RunRecord.start_time).desc()).limit(int(limit))
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
            for r in rows:
                session.expunge(r)
            return rows

    def list_runs(
        self,
        *,
        cycle: str | None = None,
        before: datetime | None = None,
        limit: int = 30,
    ) -> list[RunRecord]:
        stmt = select(RunRecord)
        if cycle is not None:
            stmt = stmt.where(RunRecord.cycle == cycle)
        if before is not None:
            stmt = stmt.where(col(RunRecord.start_time) < before)
        stmt = stmt.order_by(col(RunRecord.start_time).desc()).limit(max(0, int(limit)))
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
            for r in rows:
                session.expunge(r)
            return rows

    def latest_run_per_cycle(self) -> list[RunRecord]:
        newest = (
            select(RunRecord.cycle, func.max(RunRecord.start_time).label("start_time"))
            .where(col(RunRecord.cycle).is_not(None))
            .group_by(RunRecord.cycle)
            .subquery()
        )
        stmt = (
            select(RunRecord)
            .join(
                newest,
                and_(
                    col(RunRecord.cycle) == newest.c.cycle,
                    col(RunRecord.start_time) == newest.c.start_time,
                ),
            )
            .order_by(col(RunRecord.start_time).desc())
        )
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
            seen: set[str] = set()
            out: list[RunRecord] = []
            for r in rows:  # two runs of a cycle in the same microsecond: keep one
                if r.cycle in seen:
                    continue
                seen.add(r.cycle or "")
                session.expunge(r)
                out.append(r)
            return out

    def list_runs_by_status(self, status: RunStatus) -> list[RunRecord]:
        stmt = select(RunRecord).where(RunRecord.status == status)
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
            for r in rows:
                session.expunge(r)
            return rows

    def list_run_keys(self) -> list[tuple[str, str | None, datetime | None, RunStatus]]:
        stmt = select(
            RunRecord.id, RunRecord.cycle, RunRecord.start_time, RunRecord.status
        ).order_by(col(RunRecord.start_time).desc())
        with Session(self._engine) as session:
            return [
                (str(run_id), cycle, start_time, RunStatus(status))
                for run_id, cycle, start_time, status in session.exec(stmt).all()
            ]

    def delete_runs(self, run_ids: list[str]) -> int:
        ids: list[uuid.UUID] = []
        for run_id in run_ids:
            try:
                ids.append(uuid.UUID(str(run_id)))
            except ValueError:
                continue  # not a run id this repository could have issued
        if not ids:
            return 0
        deleted = 0
        with Session(self._engine) as session:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                result = session.exec(  # type: ignore[call-overload]
                    delete(RunRecord).where(col(RunRecord.id).in_(chunk))
                )
                deleted += int(result.rowcount or 0)
            session.commit()
        return deleted

    def bulk_update(self, records: list[RunRecord]) -> int:
        if not records:
            return 0
        with Session(self._engine) as session:
            for r in records:
                sync_promoted_columns(r)
                session.merge(r)
            session.commit()
            return len(records)
