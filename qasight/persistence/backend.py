"""PersistenceBackend protocol — the contract every backend must satisfy."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from qasight.engine.result import PlanResult


@runtime_checkable
class PersistenceBackend(Protocol):
    """Storage backend for engine plan results."""

    def persist(self, result: PlanResult) -> str | None:
        """Best-effort write of *result* to the backing store.

        Implementations MUST NOT raise — persistence failures are logged
        but never propagate to the caller. Implementations MAY return an
        opaque backend-assigned id (e.g. the DB run id) on success.
        """
        ...
