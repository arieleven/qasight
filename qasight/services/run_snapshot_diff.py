"""Per-test diff between two completed runs, using each run's own artifact
snapshot -- no ``ReportArchive`` row required (run-history runs and archived
cycle reports are two separate, unlinked id spaces in this codebase).
"""

from __future__ import annotations

from pathlib import Path

from qasight.history.snapshots import snapshot_files_for_download
from qasight.history.views import CompletedRunView
from qasight.services.report_archive_diff import CaseChange, _load_cases, diff_case_maps


def diff_run_snapshots(
    *, baseline: CompletedRunView, current: CompletedRunView, tmp: Path
) -> list[CaseChange]:
    """Materialize both runs' snapshots under ``tmp`` and return case-level changes."""
    base_root = _materialize_snapshot(record=baseline, dest=tmp / "baseline")
    cur_root = _materialize_snapshot(record=current, dest=tmp / "current")
    base_cases = _load_cases(base_root)
    cur_cases = _load_cases(cur_root)
    return diff_case_maps(base_cases, cur_cases)


def _materialize_snapshot(*, record: CompletedRunView, dest: Path) -> Path:
    """Write a run's snapshot files to a plain local dir.

    Reuses ``snapshot_files_for_download`` (the "download run artifacts" feature)
    instead of re-implementing snapshot access here.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for rel_label, data in snapshot_files_for_download(record=record):
        path = dest / rel_label
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return dest
