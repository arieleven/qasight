"""Tests for the run-snapshot-based per-test diff (no ReportArchive needed)."""

from __future__ import annotations

import json

from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import run_snapshot_diff


def _run(run_id: str) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind="pytest",
        returncode=0,
        wall_duration_ms=1000.0,
        metrics_duration_ms=900,
        total_tests=2,
        passed=2,
        failed=0,
        broken=0,
        skipped=0,
        avg_case_ms=100.0,
        health_pct=100.0,
        target_repo="/tmp/repo",
        snapshot_dir=f"runs/{run_id}/artifacts",
        audit_json=None,
    )


def _result_bytes(
    *, history_id: str, status: str, name: str, start: int = 0, stop: int = 100
) -> bytes:
    return json.dumps(
        {"historyId": history_id, "status": status, "name": name, "start": start, "stop": stop}
    ).encode("utf-8")


def test_diff_run_snapshots_classifies_regression_and_added(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    baseline = _run("baseline")
    current = _run("current")

    def _fake_snapshot_files(*, record: CompletedRunView):
        if record.run_id == "baseline":
            return [
                (
                    "st/allure-results/pytest/a-result.json",
                    _result_bytes(history_id="a", status="passed", name="test_a"),
                ),
                (
                    "st/allure-results/pytest/b-result.json",
                    _result_bytes(history_id="b", status="passed", name="test_b"),
                ),
            ]
        return [
            (
                "st/allure-results/pytest/a-result.json",
                _result_bytes(history_id="a", status="failed", name="test_a"),
            ),
            (
                "st/allure-results/pytest/b-result.json",
                _result_bytes(history_id="b", status="passed", name="test_b"),
            ),
            (
                "st/allure-results/pytest/c-result.json",
                _result_bytes(history_id="c", status="passed", name="test_c"),
            ),
        ]

    monkeypatch.setattr(run_snapshot_diff, "snapshot_files_for_download", _fake_snapshot_files)

    changes = run_snapshot_diff.diff_run_snapshots(baseline=baseline, current=current, tmp=tmp_path)
    by_key = {c.key: c for c in changes}

    assert by_key["a"].kind == "regression"
    assert by_key["a"].baseline_status == "passed"
    assert by_key["a"].current_status == "failed"
    assert by_key["c"].kind == "added"
    assert "b" not in by_key  # unchanged cases are dropped, matching diff_archives' contract
