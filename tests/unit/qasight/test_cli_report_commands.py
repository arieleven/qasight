"""``qasight report native`` and ``qasight report pyramid`` (no browser, no database)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _cycle(tmp_path: Path) -> Path:
    cycle = tmp_path / "artifacts" / "smoke"
    br = cycle / "ui" / "behave_reports"
    br.mkdir(parents=True)
    (br / "report.html").write_text("<html/>", encoding="utf-8")
    (cycle / "ui" / "run.log").write_text("ok\n", encoding="utf-8")
    (cycle / "api" / "allure-results").mkdir(parents=True)
    (cycle / "events.ndjson").write_text("{}\n", encoding="utf-8")
    (cycle / "plan_result.json").write_text(
        json.dumps(
            {
                "stages": [
                    {"name": "ui", "framework": "behavex"},
                    {"name": "api", "framework": "pytest"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return cycle


def _native(runner: CliRunner, tmp_path: Path, *args: str):  # noqa: ANN202
    return runner.invoke(
        app,
        ["report", "--artifacts", str(tmp_path / "artifacts"), "--cycle", "smoke", "native", *args],
    )


def test_native_lists_reports_without_opening(runner: CliRunner, tmp_path: Path) -> None:
    _cycle(tmp_path)
    result = _native(runner, tmp_path, "--no-open")
    assert result.exit_code == 0, result.output
    assert "Native reports" in result.output
    assert "ui" in result.output and "behavex" in result.output


def test_native_opens_html_reports(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cycle(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr(
        "qasight.reporting.native_reports.open_native_uri",
        lambda path: opened.append(path) or True,
    )
    result = _native(runner, tmp_path)
    assert result.exit_code == 0, result.output
    assert [p.name for p in opened] == ["report.html"]


def test_native_browser_failure_exits_3(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cycle(tmp_path)
    monkeypatch.setattr("qasight.reporting.native_reports.open_native_uri", lambda path: False)  # noqa: ARG005
    result = _native(runner, tmp_path)
    assert result.exit_code == 3
    assert "Could not open browser" in result.output


def test_native_single_routine_prints_its_path(runner: CliRunner, tmp_path: Path) -> None:
    _cycle(tmp_path)
    result = _native(runner, tmp_path, "ui", "--no-open")
    assert result.exit_code == 0, result.output
    assert "report.html" in result.output


def test_native_unknown_routine_and_missing_cycle_exit_2(runner: CliRunner, tmp_path: Path) -> None:
    _cycle(tmp_path)
    unknown = _native(runner, tmp_path, "nope", "--no-open")
    assert unknown.exit_code == 2
    assert "Unknown routine" in unknown.output
    missing = runner.invoke(
        app, ["report", "--artifacts", str(tmp_path / "empty"), "--cycle", "x", "native"]
    )
    assert missing.exit_code == 2
    assert "No cycle artifacts found" in missing.output


def _run(stage_health: list[dict]) -> CompletedRunView:
    return CompletedRunView(
        run_id="run-1",
        status=RunStatus.COMPLETED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind="cycle",
        returncode=0,
        wall_duration_ms=1000.0,
        metrics_duration_ms=1000,
        total_tests=None,
        passed=None,
        failed=None,
        broken=None,
        skipped=None,
        avg_case_ms=None,
        health_pct=None,
        target_repo=None,
        snapshot_dir=None,
        audit_json=None,
        cycle="smoke",
        stage_health=stage_health,
    )


def test_pyramid_renders_recorded_tiers(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run(
        [
            {"name": "unit", "tier": "unit", "total_tests": 8},
            {"name": "api", "tier": "integration", "total_tests": 3},
            {"name": "ui", "tier": "e2e", "total_tests": 1},
        ]
    )
    monkeypatch.setattr("qasight.history.read_model.get_run", lambda run_id: run)  # noqa: ARG005
    result = runner.invoke(app, ["report", "pyramid", "run-1"])
    assert result.exit_code == 0, result.output
    assert "Test Pyramid" in result.output
    assert "unit=8 integration=3 e2e=1" in result.output


def test_pyramid_unknown_run_exits_2(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("qasight.history.read_model.get_run", lambda run_id: None)  # noqa: ARG005
    result = runner.invoke(app, ["report", "pyramid", "nope"])
    assert result.exit_code == 2
    assert "no run found" in result.output
