"""Triage memory: mark why a failure happens once, and later failures like it inherit the cause.

A cause belongs to a cycle and a failure message, compared by
:func:`~qasight.services.run_failures.message_signature` (numbers, hex ids
and quoted values blanked), the same rule that groups a run's failures under
"Same cause". So marking ``ConnectionRefusedError: port 5432`` as an
environment problem labels every later test in the cycle that fails with it,
on any run, until someone clears it.

Causes live in the run-history database (table ``failure_triage``), next to the
runs, and are read by :func:`~qasight.services.run_failures.load_run_failures`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from qasight.services.run_failures import message_signature

# Kept short and fixed so a dashboard can count them; the note says the rest.
CAUSES: Final[dict[str, str]] = {
    "product_bug": "Bug in the product",
    "test_bug": "Bug in the test",
    "environment": "Environment or infrastructure",
    "flaky": "Flaky test",
    "other": "Other",
}
MAX_NOTE = 500


class TriageError(ValueError):
    """An unknown cause, or a failure with no message to match on."""


@dataclass(frozen=True)
class Triage:
    cycle: str
    signature: str
    cause: str
    note: str
    example_message: str
    run_id: str
    test_key: str
    marked_by: str | None
    marked_at: datetime


def _id(cycle: str, signature: str) -> str:
    return hashlib.sha256(f"{cycle}\n{signature}".encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _triage(row: Any) -> Triage:
    return Triage(
        cycle=row.cycle,
        signature=row.signature,
        cause=row.cause,
        note=row.note,
        example_message=row.example_message,
        run_id=row.run_id,
        test_key=row.test_key,
        marked_by=row.marked_by,
        marked_at=_aware(row.marked_at),
    )


class TriageStore:
    """Failure causes on a SQLAlchemy engine (the run-history DB by default)."""

    def __init__(self, engine: Any = None) -> None:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        self._engine = engine if engine is not None else get_engine()
        create_db_and_tables(self._engine)

    def mark(
        self,
        *,
        cycle: str,
        message: str,
        cause: str,
        note: str = "",
        run_id: str = "",
        test_key: str = "",
        marked_by: str | None = None,
    ) -> Triage:
        """Set the cause of ``message``'s failures in ``cycle``, replacing any earlier one."""
        from sqlmodel import Session

        from qasight.repository.models import FailureTriage

        if cause not in CAUSES:
            raise TriageError(f"cause must be one of {', '.join(CAUSES)}")
        signature = message_signature(message)[:1024]
        if not cycle.strip() or not signature:
            raise TriageError("a cause needs a cycle and a failure message to match on")
        row = FailureTriage(
            id=_id(cycle, signature),
            cycle=cycle[:512],
            signature=signature,
            cause=cause,
            note=note.strip()[:MAX_NOTE],
            example_message=message[:1000],
            run_id=run_id[:64],
            test_key=test_key[:200],
            marked_by=marked_by[:64] if marked_by else None,
            marked_at=datetime.now(tz=UTC),
        )
        with Session(self._engine) as session:
            session.merge(row)
            session.commit()
        return _triage(row)

    def clear(self, *, cycle: str, signature: str) -> bool:
        """Forget the cause; False if there was none."""
        from sqlmodel import Session

        from qasight.repository.models import FailureTriage

        with Session(self._engine) as session:
            row = session.get(FailureTriage, _id(cycle, signature))
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def for_messages(self, cycle: str, messages: Iterable[str]) -> dict[str, Triage]:
        """The causes marked in ``cycle`` for these messages, by message (only those that have one)."""
        from sqlmodel import Session, col, select

        from qasight.repository.models import FailureTriage

        # Messages that differ only in numbers or ids share a signature, so one row answers several.
        by_id: dict[str, list[str]] = {}
        for message in messages:
            if message:
                by_id.setdefault(_id(cycle, message_signature(message)), []).append(message)
        if not by_id:
            return {}
        with Session(self._engine) as session:
            rows = session.exec(
                select(FailureTriage).where(col(FailureTriage.id).in_(list(by_id)))
            ).all()
            return {message: _triage(row) for row in rows for message in by_id[row.id]}

    def list(self, cycle: str | None = None) -> list[Triage]:
        """Every cause, newest first, optionally only ``cycle``'s."""
        from sqlmodel import Session, col, select

        from qasight.repository.models import FailureTriage

        query = select(FailureTriage).order_by(col(FailureTriage.marked_at).desc())
        if cycle:
            query = query.where(FailureTriage.cycle == cycle)
        with Session(self._engine) as session:
            return [_triage(row) for row in session.exec(query).all()]
