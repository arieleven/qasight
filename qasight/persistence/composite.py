"""Composite backend — fans out to multiple backends in sequence."""

from __future__ import annotations

import logging
from pathlib import Path

from qasight.engine.result import PlanResult
from qasight.persistence.backend import PersistenceBackend
from qasight.persistence.json_backend import JsonBackend

logger = logging.getLogger(__name__)


class _CompositeBackend:
    """Run every registered backend; swallow individual failures."""

    def __init__(self, backends: list[PersistenceBackend]) -> None:
        self._backends = backends

    def persist(self, result: PlanResult) -> str | None:
        run_id: str | None = None
        for backend in self._backends:
            try:
                outcome = backend.persist(result)
            except Exception:
                # Broad on purpose: one failing backend must not stop the others.
                logger.warning("backend %s failed", type(backend).__name__, exc_info=True)
                continue
            if isinstance(outcome, str) and outcome:
                run_id = outcome
        return run_id


def composite_backend(*, artifacts_root: Path, db: bool = True) -> _CompositeBackend:
    """Build the default persistence stack.

    Always includes :class:`JsonBackend`.  Includes :class:`DbBackend` when
    *db* is ``True`` and the repository layer is importable.
    """

    backends: list[PersistenceBackend] = [JsonBackend(artifacts_root)]
    if db:
        try:
            from qasight.persistence.db_backend import DbBackend

            backends.append(DbBackend(artifacts_root))
        except ImportError:
            logger.debug("db backend unavailable, skipping", exc_info=True)
    return _CompositeBackend(backends)
