"""A recorded run ends with what's new in it (qasight/services/run_news.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

_CASE = '<testcase classname="t" name="{name}" time="{time}">{body}</testcase>'


def _junit(path: Path, cases: dict[str, tuple[str, float]]) -> None:
    body = "".join(
        _CASE.format(
            name=name,
            time=secs,
            body='<failure message="boom">boom</failure>' if status == "failed" else "",
        )
        for name, (status, secs) in cases.items()
    )
    path.write_text(f'<testsuite name="t" tests="{len(cases)}">{body}</testsuite>')


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'testo_history.db'}")
    monkeypatch.setenv("TESTO_HOME", str(tmp_path / ".testo"))
    monkeypatch.delenv("TESTO_RUN_NEWS", raising=False)
    monkeypatch.chdir(tmp_path)
    from qasight.repository.db_config import reset_engine_cache

    reset_engine_cache()
    return tmp_path


def _ingest(runner: CliRunner, project: Path, cases: dict[str, tuple[str, float]], *extra: str):
    report = project / "r.xml"
    _junit(report, cases)
    return runner.invoke(app, ["ingest", "r.xml", "--cycle", "news", *extra])


def test_second_run_names_the_new_failure_and_the_fix(project: Path) -> None:
    runner = CliRunner()
    _ingest(runner, project, {"test_a": ("passed", 0.1), "test_b": ("failed", 0.1)})
    result = _ingest(runner, project, {"test_a": ("failed", 0.1), "test_b": ("passed", 0.1)})
    assert "New failure   test_a: boom" in result.output
    assert "Fixed         test_b" in result.output


def test_ci_puts_the_news_on_plan_finished_which_stays_last(project: Path) -> None:
    runner = CliRunner()
    _ingest(runner, project, {"test_a": ("passed", 0.1)}, "--ci")
    result = _ingest(runner, project, {"test_a": ("failed", 0.1)}, "--ci")
    events = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
    assert events[-1]["event"] == "plan_finished"
    news = events[-1]["news"]
    assert news["cycle"] == "news"
    assert [t["name"] for t in news["new_failures"]] == ["test_a"]
    assert news["counts"]["new_failures"] == 1


def test_news_can_be_turned_off(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_RUN_NEWS", "0")
    result = _ingest(CliRunner(), project, {"test_a": ("passed", 0.1)})
    assert "Details: qasight summary" not in result.output
