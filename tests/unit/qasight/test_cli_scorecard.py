"""``qasight scorecard tools|import|show|scan``: switches, recording a report, the release gate's exit codes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.repository.db_config import reset_engine_cache

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

runner = CliRunner()


@pytest.fixture(autouse=True)
def _history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    for name in ("TESTO_SERVER_URL", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    reset_engine_cache()
    yield
    reset_engine_cache()


def _bandit(tmp_path: Path, highs: int) -> Path:
    path = tmp_path / f"bandit-{highs}.json"
    results = [
        {
            "issue_severity": "HIGH",
            "test_id": f"B{i}",
            "test_name": "x",
            "issue_text": "t",
            "filename": "a.py",
            "line_number": i,
        }
        for i in range(highs)
    ]
    path.write_text(json.dumps({"results": results}), encoding="utf-8")
    return path


def test_tools_on_off_and_json() -> None:
    result = runner.invoke(
        app, ["scorecard", "tools", "--off", "bandit,ruff", "--on", "locust", "--json"]
    )
    assert result.exit_code == 0, result.output
    rows = {r["id"]: r for r in json.loads(result.output)}
    assert (rows["bandit"]["enabled"], rows["ruff"]["enabled"], rows["locust"]["enabled"]) == (
        False,
        False,
        True,
    )
    assert runner.invoke(app, ["scorecard", "tools", "--off", "nope"]).exit_code == 2
    assert "on   bandit" in runner.invoke(app, ["scorecard", "tools", "--reset"]).output


def test_import_then_show_and_gate(tmp_path: Path) -> None:
    for version, highs in (("1.0.0", 0), ("1.1.0", 3)):
        result = runner.invoke(
            app,
            [
                "scorecard",
                "import",
                "bandit",
                str(_bandit(tmp_path, highs)),
                "--project",
                "app",
                "--version",
                version,
            ],
        )
        assert result.exit_code == 0, result.output
    assert "app 1.1.0: Bandit 77/100" in result.output

    shown = runner.invoke(app, ["scorecard", "show"])
    assert shown.exit_code == 0
    assert "app 1.1.0: 77/100 (C)  (-23)" in shown.output

    assert runner.invoke(app, ["scorecard", "show", "--min-score", "70"]).exit_code == 0
    gate = runner.invoke(app, ["scorecard", "show", "--min-score", "80"])
    assert gate.exit_code == 1
    assert "overall score 77 is below 80" in gate.output
    assert runner.invoke(app, ["scorecard", "show", "--no-drop"]).exit_code == 1

    out = tmp_path / "card.md"
    assert (
        runner.invoke(app, ["scorecard", "show", "--format", "markdown", "-o", str(out)]).exit_code
        == 0
    )
    assert out.read_text(encoding="utf-8").startswith("# Release scorecard: app 1.1.0")
    as_json = json.loads(
        runner.invoke(app, ["scorecard", "show", "--format", "json", "--version", "1.0.0"]).output
    )
    assert as_json["score"] == 100


def test_import_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert runner.invoke(app, ["scorecard", "import", "nope", str(bad)]).exit_code == 2
    assert (
        runner.invoke(
            app, ["scorecard", "import", "bandit", str(tmp_path / "missing.json")]
        ).exit_code
        == 2
    )
    result = runner.invoke(
        app, ["scorecard", "import", "bandit", str(bad), "--project", "a", "--version", "1"]
    )
    assert result.exit_code == 2
    assert "expected Bandit's JSON report" in result.output


def test_show_with_nothing_scored() -> None:
    result = runner.invoke(app, ["scorecard", "show"])
    assert result.exit_code == 1
    assert "qasight scorecard scan" in result.output


def test_scan_with_no_tool_to_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app, ["scorecard", "scan", str(repo), "--tools", "coverage", "--version", "1"]
    )
    assert result.exit_code == 1
    assert "Coverage: import only" in result.output
    assert runner.invoke(app, ["scorecard", "scan", str(tmp_path / "missing")]).exit_code == 2
