"""Raw artifact files captured for a run (``RunRecord.snapshot_dir``)."""

from __future__ import annotations

from pathlib import Path

from qasight import paths
from qasight.history.views import CompletedRunView


def snapshot_file_names(*, record: CompletedRunView) -> list[str]:
    """Relative paths of the files in the run's snapshot, sorted, without reading them."""
    base = _snapshot_base(record)
    if base is None:
        return []
    return [str(p.relative_to(base)) for p in sorted(base.rglob("*")) if p.is_file()]


def snapshot_url_base(*, record: CompletedRunView) -> str | None:
    """URL prefix (relative to the API's ``/``) the snapshot's files are served under.

    ``None`` when the snapshot is not under ``static/history/``, the only folder the
    API serves (at ``/history``).
    """
    base = _snapshot_base(record)
    if base is None:
        return None
    history = paths.STATIC_HISTORY_ROOT.resolve()
    resolved = base.resolve()
    if history not in resolved.parents:
        return None
    return "history/" + resolved.relative_to(history).as_posix()


def _snapshot_base(record: CompletedRunView) -> Path | None:
    if not record.snapshot_dir:
        return None
    base = paths.ORCHESTRATOR_ROOT / record.snapshot_dir
    return base if base.is_dir() else None


def snapshot_files_for_download(*, record: CompletedRunView) -> list[tuple[str, bytes]]:
    """``(relative_path, bytes)`` for each file in the run's snapshot, sorted by path.

    ``snapshot_dir`` is relative to ``paths.ORCHESTRATOR_ROOT`` (the data root).
    """
    base = _snapshot_base(record)
    if base is None:
        return []
    return [
        (str(p.relative_to(base)), p.read_bytes()) for p in sorted(base.rglob("*")) if p.is_file()
    ]
