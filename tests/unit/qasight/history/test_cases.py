"""``cases_for_run``: the ``test_result`` rows, else the run's snapshot, which it then stores."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from qasight import paths
from qasight.history import cases as cases_mod
from qasight.history.views import CompletedRunView
from qasight.repository.db import get_test_result_store
from qasight.repository.models import RunStatus

pytestmark = pytest.mark.tier_fast


def _run(run_id: str) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind="cycle",
        returncode=0,
        wall_duration_ms=1000.0,
        metrics_duration_ms=None,
        total_tests=None,
        passed=None,
        failed=None,
        broken=None,
        skipped=None,
        avg_case_ms=None,
        health_pct=None,
        target_repo=None,
        snapshot_dir=f"static/history/{run_id}/artifacts",
        audit_json=None,
        cycle="unit",
    )


def _write_snapshot(root: Path, run: CompletedRunView, statuses: dict[str, str]) -> Path:
    folder = root / str(run.snapshot_dir) / "unit" / "allure-results" / "pytest"
    folder.mkdir(parents=True)
    for name, status in statuses.items():
        (folder / f"{name}-result.json").write_text(
            json.dumps({"historyId": name, "name": name, "status": status}), encoding="utf-8"
        )
    return root / str(run.snapshot_dir)


def test_an_old_run_is_read_from_its_snapshot_once_then_from_the_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    run = _run("old")
    snapshot = _write_snapshot(tmp_path, run, {"test_a": "passed", "test_b": "failed"})

    first = cases_mod.cases_for_run(run)
    assert {k: c["status"] for k, c in first.items()} == {"test_a": "passed", "test_b": "failed"}
    assert get_test_result_store().has_rows("old")

    shutil.rmtree(snapshot)  # the files are no longer needed
    assert cases_mod.cases_for_run(run) == first


def test_stored_rows_win_over_the_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    run = _run("new")
    _write_snapshot(tmp_path, run, {"test_a": "failed"})
    get_test_result_store().replace("new", {"test_a": {"name": "test_a", "status": "passed"}})

    assert cases_mod.cases_for_run(run)["test_a"]["status"] == "passed"


def test_a_run_without_snapshot_or_rows_has_no_cases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    assert cases_mod.cases_for_run(replace(_run("none"), snapshot_dir=None)) == {}
    assert cases_mod.cases_for_run(_run("gone")) == {}  # snapshot_dir set, folder missing
    assert not get_test_result_store().has_rows("gone")


def test_a_failed_store_write_does_not_fail_the_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    run = _run("fragile")
    _write_snapshot(tmp_path, run, {"test_a": "passed"})

    class _Broken:
        def cases(self, run_id: str) -> dict[str, dict[str, object]]:
            return {}

        def replace(self, run_id: str, cases: object) -> int:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(cases_mod, "get_test_result_store", lambda: _Broken())
    assert list(cases_mod.cases_for_run(run)) == ["test_a"]
