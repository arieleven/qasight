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


def history_of(
    runs: list[CompletedRunView], keys: set[str]
) -> dict[str, dict[str, dict[str, Any]]]:
    """``keys``' cases in each of ``runs``, as ``{run_id: {key: case}}``.

    Every run that has tests gets an entry, empty when none of ``keys`` ran in it,
    so a caller can tell "didn't run" from "run has no tests" (which is left out).
    One query for the lot instead of every row of every run.
    """
    if not runs:
        return {}
    store = get_test_result_store()
    ids = [r.run_id for r in runs]
    found = store.cases_for_keys(ids, keys) if keys else {}
    stored = store.runs_with_rows(ids)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for run in runs:
        if run.run_id[:64] in stored:
            out[run.run_id] = found.get(run.run_id[:64], {})
            continue
        # No rows yet: an older run with only its snapshot; adopting it stores them.
        cases = cases_for_run(run)
        if cases:
            out[run.run_id] = {k: cases[k] for k in keys if k in cases}
    return out
