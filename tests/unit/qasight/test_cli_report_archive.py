"""CLI tests for ``qasight report list`` and ``qasight report open`` (database archives)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.repository.db import get_report_archive_repository, reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight.services.report_archive import build_cycle_zip_bytes


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _minimal_cycle_artifacts(base: Path, plan: str = "cyc") -> bytes:
    root = base / "artifacts"
    plan_dir = root / plan
    (plan_dir / "st1" / "allure-results" / "pytest").mkdir(parents=True)
    (plan_dir / "st1" / "allure-results" / "pytest" / "a-result.json").write_text(
        '{"name":"x","status":"passed"}', encoding="utf-8"
    )
    (plan_dir / "plan_result.json").write_text(
        json.dumps({"plan": plan, "exit_code": 0, "stages": []}),
        encoding="utf-8",
    )
    (plan_dir / "events.ndjson").write_text('{"event":"plan_started"}\n', encoding="utf-8")
    blob, _, _ = build_cycle_zip_bytes(root, plan)
    return blob


def test_report_list_empty(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_repository_cache()
    reset_engine_cache()
    try:
        result = runner.invoke(app, ["report", "list"])
    finally:
        reset_repository_cache()
        reset_engine_cache()
    assert result.exit_code == 0
    assert "No archived reports" in result.stdout or "no archived" in result.stdout.lower()


def test_report_list_and_open_json(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_repository_cache()
    reset_engine_cache()
    try:
        blob = _minimal_cycle_artifacts(tmp_path)
        row = get_report_archive_repository().insert(
            cycle_name="cyc",
            exit_code=0,
            summary_json={"plan": "cyc"},
            artifact_bytes=blob,
        )

        r_list = runner.invoke(app, ["report", "list", "--limit", "5"])
        assert r_list.exit_code == 0, r_list.stdout
        assert "cyc" in r_list.stdout
        assert str(row.id) in r_list.stdout

        summary_path = tmp_path / "db-summary.json"
        r_open = runner.invoke(
            app,
            [
                "report",
                "open",
                "--id",
                str(row.id),
                "--format",
                "json",
                "--generate-only",
                "--summary-out",
                str(summary_path),
            ],
        )
        assert r_open.exit_code == 0, r_open.stdout + r_open.stderr
        assert summary_path.is_file()
    finally:
        reset_repository_cache()
        reset_engine_cache()
