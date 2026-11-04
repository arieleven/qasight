"""A run that dies holding its cycle lock is recorded as failed, not lost."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from qasight.engine.cycle_lock import RunMarker
from qasight.history import maintenance
from qasight.history.read_model import list_cycle_runs
from qasight.repository.db import get_repository
from qasight.repository.models import RunStatus

pytestmark = pytest.mark.tier_fast


def _left_marker(root: Path, cycle: str) -> RunMarker:
    marker = RunMarker(cycle=cycle, pid=4242, host="ci-box", started_at=time.time() - 60)
    (root / cycle).mkdir(parents=True)
    (root / cycle / ".lock").write_text(json.dumps(marker.__dict__))
    return marker


def test_record_unfinished_run_writes_a_failed_run_with_the_reason(tmp_path: Path) -> None:
    marker = RunMarker(cycle="web", pid=7, host="box", started_at=time.time() - 30)
    run_id = maintenance.record_unfinished_run(marker)
    assert run_id is not None
    record = get_repository().get_run(run_id)
    assert record is not None and record.status == RunStatus.FAILED
    assert record.cycle == "web"
    md = record.metadata_ or {}
    assert md["error"] == "interrupted"
    assert "stopped without finishing" in md["error_message"] and "box" in md["error_message"]
    assert [r.run_id for r in list_cycle_runs("web")] == [run_id]


def test_recover_unfinished_runs_records_each_left_marker_once(tmp_path: Path) -> None:
    _left_marker(tmp_path, "web")
    _left_marker(tmp_path, "api")
    (tmp_path / "unit").mkdir()
    (tmp_path / "unit" / ".lock").write_text("")  # finished cleanly

    assert maintenance.recover_unfinished_runs(tmp_path) == 2
    assert maintenance.recover_unfinished_runs(tmp_path) == 0
    assert {r.cycle for r in (list_cycle_runs("web") + list_cycle_runs("api"))} == {"web", "api"}


def test_recover_unfinished_runs_without_an_artifacts_folder(tmp_path: Path) -> None:
    assert maintenance.recover_unfinished_runs(tmp_path / "missing") == 0
