"""One run's tests, for the analytics: from the ``test_result`` table, else its snapshot.

Runs recorded since revision ``0003`` have rows written by ``DbBackend``. Older
runs have only their Allure snapshot on disk; the first read parses it and
stores the rows, so the next read is a query and the API no longer needs the
run's files for flaky detection, per-test history or failures.
"""

from __future__ import annotations

import logging
from typing import Any

from qasight import paths
from qasight.history.views import CompletedRunView
from qasight.reporting.allure_cases import load_cases
from qasight.repository.db import get_test_result_store

logger = logging.getLogger(__name__)


def cases_for_run(run: CompletedRunView) -> dict[str, dict[str, Any]]:
    """``run``'s tests as ``{key: case}`` (see :func:`load_cases`); ``{}`` when it has none."""
    store = get_test_result_store()
    stored = store.cases(run.run_id)
    if stored:
        return stored
    if not run.snapshot_dir:
        return {}
    cases = load_cases(paths.ORCHESTRATOR_ROOT / run.snapshot_dir)
    if cases:
        try:
            store.replace(run.run_id, cases)
        except Exception:  # noqa: BLE001 - a failed adoption must not fail the read
            logger.warning("could not store test rows for run %s", run.run_id, exc_info=True)
    return cases
