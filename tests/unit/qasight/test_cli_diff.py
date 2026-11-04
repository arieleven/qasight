"""CLI regression tests for archived report diff and summary commands."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.engine.exit_codes import EngineExitCode
from qasight.repository.db import get_report_archive_repository, reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight.repository.report_archive_repository import SQLReportArchiveRepository
from qasight.services.report_archive import build_cycle_zip_bytes


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def sqlite_report_repo(monkeypatch: pytest.MonkeyPatch) -> SQLReportArchiveRepository:
    reset_repository_cache()
    reset_engine_cache()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    try:
        yield get_report_archive_repository()
    finally:
        reset_repository_cache()
        reset_engine_cache()


def test_diff_metrics_only_uses_archive_columns_without_unzipping(
    runner: CliRunner,
    tmp_path: Path,
    sqlite_report_repo: SQLReportArchiveRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    baseline = sqlite_report_repo.insert(
        cycle_name="checkout",
        exit_code=0,
        summary_json={"plan": "checkout"},
        artifact_bytes=b"not a zip",
        total_tests=3,
        passed=3,
        failed=0,
        plan_duration_ms=1200,
    )
    current = sqlite_report_repo.insert(
        cycle_name="checkout",
        exit_code=1,
        summary_json={"plan": "checkout"},
        artifact_bytes=b"still not a zip",
        total_tests=5,
        passed=4,
        failed=1,
        plan_duration_ms=1500,
    )

    result = runner.invoke(
        app,
        [
            "diff",
            "--metrics-only",
            str(baseline.id),
            str(current.id),
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Run metrics" in result.stdout
    assert "total_tests" in result.stdout
    assert "plan_duration_ms" in result.stdout
    assert "300" in result.stdout


def _cycle_blob(base: Path, plan: str, *, status: str) -> bytes:
    root = base / f"artifacts-{plan}-{status}"
    result_dir = root / plan / "stage-a" / "allure-results" / "pytest"
    result_dir.mkdir(parents=True)
    (root / plan / "events.ndjson").write_text("{}\n", encoding="utf-8")
    (root / plan / "plan_result.json").write_text(
        json.dumps({"plan": plan, "exit_code": 0 if status == "passed" else 1, "duration_s": 1.0}),
        encoding="utf-8",
    )
    (result_dir / "case-result.json").write_text(
        json.dumps(
            {
                "historyId": f"{plan}-case",
                "name": f"{plan} case",
                "status": status,
                "start": 100,
                "stop": 250,
            }
        ),
        encoding="utf-8",
    )
    blob, _summary, _exit_code = build_cycle_zip_bytes(root, plan)
    return blob


def test_diff_rejects_invalid_archive_uuid(runner: CliRunner) -> None:
    result = runner.invoke(app, ["diff", "not-a-uuid", "also-not-a-uuid"])

    assert result.exit_code == int(EngineExitCode.INVALID_INPUT)
    assert "valid report archive UUID" in result.stdout


def test_summary_cycle_filters_to_two_most_recent_for_cycle(
    runner: CliRunner,
    tmp_path: Path,
    sqlite_report_repo: SQLReportArchiveRepository,
) -> None:
    older_blob = _cycle_blob(tmp_path / "older", "cycle-a", status="passed")
    newer_blob = _cycle_blob(tmp_path / "newer", "cycle-a", status="failed")
    other_blob = _cycle_blob(tmp_path / "other", "cycle-b", status="passed")

    older = sqlite_report_repo.insert(
        cycle_name="cycle-a",
        exit_code=0,
        summary_json={"plan": "cycle-a"},
        artifact_bytes=older_blob,
        total_tests=1,
        passed=1,
        failed=0,
        allure_duration_ms=150,
        plan_duration_ms=1000,
    )
    time.sleep(0.02)
    newer = sqlite_report_repo.insert(
        cycle_name="cycle-a",
        exit_code=1,
        summary_json={"plan": "cycle-a"},
        artifact_bytes=newer_blob,
        total_tests=1,
        passed=0,
        failed=1,
        allure_duration_ms=150,
        plan_duration_ms=1000,
    )
    time.sleep(0.02)
    unrelated = sqlite_report_repo.insert(
        cycle_name="cycle-b",
        exit_code=0,
        summary_json={"plan": "cycle-b"},
        artifact_bytes=other_blob,
        total_tests=1,
        passed=1,
        failed=0,
    )

    result = runner.invoke(app, ["summary", "--cycle", "cycle-a"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert str(older.id) in result.stdout
    assert str(newer.id) in result.stdout
    assert str(unrelated.id) not in result.stdout
    assert "regression" in result.stdout.lower()
