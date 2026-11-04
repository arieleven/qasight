"""Abstract repository interface for run history persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .models import RunRecord, RunStatus


class RunNotFoundError(LookupError):
    """Raised when a run id has no corresponding :class:`RunRecord`."""


@runtime_checkable
class BaseRunRepository(Protocol):
    """Storage-agnostic interface for :class:`RunRecord` lifecycle operations.

    Implementations MUST be safe to call from multiple threads (the API runs each
    execution on a background thread) when backed by a thread-safe SQLAlchemy engine.
    """

    def create_run(
        self,
        *,
        status: RunStatus = RunStatus.PENDING,
        metadata: dict[str, Any] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> RunRecord:
        """Insert a new :class:`RunRecord` and return the persisted row (with ``id`` assigned).

        ``start_time`` defaults to now; pass the run's real start (and end) when the run
        already finished, so ordering by ``start_time`` means by start.
        """

    def get_run(self, run_id: uuid.UUID | str) -> RunRecord | None:
        """Return the :class:`RunRecord` for the given id, or ``None`` if missing.

        Accepts the orchestrator's external string id; implementations derive a stable
        :class:`uuid.UUID` from non-UUID strings using :func:`uuid.uuid5` over the
        ``uqo-run:<run_id>`` namespace.
        """

    def update_run_status(
        self,
        run_id: uuid.UUID | str,
        *,
        status: RunStatus,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        """Update status (and shallow-merge ``metadata``) for ``run_id``, or insert if missing.

        Sets ``end_time`` to the current UTC time when ``status`` is
        :attr:`RunStatus.COMPLETED` or :attr:`RunStatus.FAILED`.
        """

    def merge_run_metadata(self, run_id: uuid.UUID | str, patch: dict[str, Any]) -> bool:
        """Shallow-merge ``patch`` into the run's metadata; return ``False`` if the run does not exist."""

    def list_recent_runs(self, *, limit: int = 30) -> list[RunRecord]:
        """Return up to ``limit`` runs ordered by ``start_time`` descending (newest first)."""

    def list_runs(
        self,
        *,
        cycle: str | None = None,
        before: datetime | None = None,
        limit: int = 30,
    ) -> list[RunRecord]:
        """Up to ``limit`` runs, newest first, filtered in SQL on the promoted columns.

        ``cycle`` keeps one cycle's runs; ``before`` keeps runs that started strictly
        earlier (for "the runs before this one"). Both use the ``(cycle, start_time)`` index.
        """

    def latest_run_per_cycle(self) -> list[RunRecord]:
        """The newest run of every cycle that has one, newest first. One query, no scan limit."""

    def list_runs_by_status(self, status: RunStatus) -> list[RunRecord]:
        """Return all runs currently in the given ``status`` (e.g. for orphan cleanup)."""

    def bulk_update(self, records: list[RunRecord]) -> int:
        """Persist updates to the given rows in one transaction; return the number of rows written."""

    def list_run_keys(self) -> list[tuple[str, str | None, datetime | None, RunStatus]]:
        """``(run_id, cycle, start_time, status)`` of every run, newest first.

        The promoted columns only, no JSON: what retention needs to decide which runs go.
        """

    def delete_runs(self, run_ids: list[str]) -> int:
        """Delete the given runs' rows; return how many were deleted. Missing ids are skipped."""
