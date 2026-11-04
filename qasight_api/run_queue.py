"""Where queued API runs wait: in memory (default) or in the database.

``TESTO_RUN_QUEUE`` picks the store:

- ``memory`` (default): nothing is written anywhere. A restart drops runs that were
  still waiting, which is fine for one developer or a small team.
- ``db``: each queued run is also a row in the run-history database (``queued_run``).
  On startup the server queues those rows again, so a restart or deploy loses
  nothing that had not started yet.

A row only lives while its run waits: the worker deletes it as it starts the run
(``claim``). A run that was already running when the process died is not repeated;
the orphaned-run cleanup marks it failed in the history. Because ``claim`` is an
atomic delete, two processes that both re-queued the same row never both run it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

RUN_QUEUE_ENV = "TESTO_RUN_QUEUE"
RunQueueKind = Literal["memory", "db"]

RunKind = Literal["cycle", "adhoc"]


@dataclass(frozen=True)
class RunRequest:
    """Everything needed to start a run later, in plain JSON-safe values."""

    kind: RunKind
    cycle: str
    config_path: str | None = None
    artifacts_root: str | None = None
    persist: bool = True
    force: bool = False
    fail_fast: bool = False
    reporter_override: tuple[str, ...] | None = None
    report_db: bool = True
    async_report_db: bool = False
    workers_override: int | None = None
    stream: bool = False
    ci: bool = True
    # Ad-hoc runs only: the one stage to run.
    framework: str | None = None
    target_repo: str | None = None
    args: tuple[str, ...] = ()
    timeout_s: float | None = None
    extra_env: Mapping[str, str] = field(default_factory=dict)
    # Cycle runs only: run just these tests, or the failing tests of a recorded run
    # (services/test_selection.py).
    tests: tuple[str, ...] = ()
    rerun_failed: str | None = None

    @staticmethod
    def path_or_none(value: Path | None) -> str | None:
        return None if value is None else str(value)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["reporter_override"] = (
            None if self.reporter_override is None else list(self.reporter_override)
        )
        data["args"] = list(self.args)
        data["extra_env"] = dict(self.extra_env)
        data["tests"] = list(self.tests)
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> RunRequest:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        if known.get("reporter_override") is not None:
            known["reporter_override"] = tuple(known["reporter_override"])
        known["args"] = tuple(known.get("args") or ())
        known["extra_env"] = dict(known.get("extra_env") or {})
        known["tests"] = tuple(known.get("tests") or ())
        return cls(**known)


@dataclass(frozen=True)
class PendingRun:
    execution_id: str
    request: RunRequest
    created_at: float


class RunStore(Protocol):
    """Durable side of the run queue. The in-process FIFO stays the source of order."""

    kind: RunQueueKind

    def add(self, execution_id: str, request: RunRequest, created_at: float) -> None: ...

    def claim(self, execution_id: str) -> bool:
        """Take a queued run before starting it; False means another process took it."""
        ...

    def pending(self) -> Sequence[PendingRun]:
        """Runs still waiting from an earlier process, oldest first."""
        ...


class MemoryRunStore:
    kind: RunQueueKind = "memory"

    def add(self, execution_id: str, request: RunRequest, created_at: float) -> None:
        return

    def claim(self, execution_id: str) -> bool:
        return True

    def pending(self) -> Sequence[PendingRun]:
        return ()


class DbRunStore:
    """Queued runs as rows of ``queued_run`` in the run-history database."""

    kind: RunQueueKind = "db"

    def __init__(self, engine: Any = None) -> None:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        self._engine = engine if engine is not None else get_engine()
        create_db_and_tables(self._engine)

    def add(self, execution_id: str, request: RunRequest, created_at: float) -> None:
        from sqlmodel import Session

        from qasight.repository.models import QueuedRun

        row = QueuedRun(
            id=execution_id,
            cycle=request.cycle,
            request_json=request.to_json(),
            created_at=datetime.fromtimestamp(created_at, tz=UTC),
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()

    def claim(self, execution_id: str) -> bool:
        from sqlmodel import Session, col, delete

        from qasight.repository.models import QueuedRun

        with Session(self._engine) as session:
            result = session.exec(delete(QueuedRun).where(col(QueuedRun.id) == execution_id))
            session.commit()
            return bool(result.rowcount)

    def pending(self) -> Sequence[PendingRun]:
        from sqlmodel import Session, col, select

        from qasight.repository.models import QueuedRun

        with Session(self._engine) as session:
            rows = session.exec(select(QueuedRun).order_by(col(QueuedRun.created_at))).all()
        runs: list[PendingRun] = []
        for row in rows:
            try:
                request = RunRequest.from_json(row.request_json)
            except (TypeError, ValueError):
                logger.warning("dropping unreadable queued run %s", row.id, exc_info=True)
                self.claim(row.id)
                continue
            created = row.created_at
            if created.tzinfo is None:  # SQLite returns naive datetimes
                created = created.replace(tzinfo=UTC)
            runs.append(PendingRun(row.id, request, created.timestamp()))
        return runs


def run_queue_kind_from_env() -> RunQueueKind:
    raw = (os.environ.get(RUN_QUEUE_ENV) or "memory").strip().lower()
    if raw in ("memory", "db"):
        return raw  # type: ignore[return-value]
    logger.warning("%s=%r is not 'memory' or 'db'; using memory", RUN_QUEUE_ENV, raw)
    return "memory"


def run_store_from_env() -> RunStore:
    if run_queue_kind_from_env() == "db":
        return DbRunStore()
    return MemoryRunStore()
