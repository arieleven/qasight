"""Branch coverage for ``qasight.reporting.metrics`` (history)."""

from __future__ import annotations

import json
from pathlib import Path

from qasight.reporting.metrics import list_run_history


def test_list_run_history_with_archive(tmp_path: Path) -> None:
    cur = tmp_path / "current"
    cur.mkdir()
    (cur / "a-result.json").write_text(
        json.dumps({"status": "passed", "start": 0, "stop": 1}),
        encoding="utf-8",
    )
    arch = tmp_path / "archive"
    ts = arch / "20260101_test_runid"
    ts.mkdir(parents=True)
    (ts / "b-result.json").write_text(
        json.dumps({"status": "failed", "start": 0, "stop": 1}),
        encoding="utf-8",
    )
    hist = list_run_history(archive_root=arch, current_results_dir=cur)
    assert len(hist) >= 1
