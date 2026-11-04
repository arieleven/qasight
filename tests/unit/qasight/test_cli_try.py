"""``qasight try`` (qasight/cli/commands/try_cmd.py): the PR comment preview leaves history alone."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.cli.commands import try_cmd

_CASE_OK = '<testcase classname="c" name="{n}" time="0.1"/>'
_CASE_BAD = (
    '<testcase classname="c" name="{n}" time="0.1"><failure message="x">x</failure></testcase>'
)


def _junit(passing: list[str], failing: list[str]) -> str:
    cases = [_CASE_OK.format(n=n) for n in passing] + [_CASE_BAD.format(n=n) for n in failing]
    return (
        '<?xml version="1.0"?><testsuites><testsuite name="s">'
        + "".join(cases)
        + "</testsuite></testsuites>\n"
    )


def _runs(db: Path) -> int:
    with sqlite3.connect(db) as conn:
        return int(conn.execute("select count(*) from runrecord").fetchone()[0])


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "reports").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)  # qasight ingest writes artifacts/ under the working directory
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{root / 'testo_history.db'}")
    monkeypatch.setenv("TESTO_HOME", str(tmp_path / "home"))
    for key in ("CI", "GITHUB_ACTIONS", "GITHUB_BASE_REF"):
        monkeypatch.delenv(key, raising=False)
    return root


def test_preview_compares_with_local_history_and_records_nothing(project: Path) -> None:
    report = project / "reports" / "junit.xml"
    report.write_text(_junit(["test_a", "test_b"], []), encoding="utf-8")
    first = CliRunner().invoke(app, ["ingest", "--repo", str(project)])
    assert first.exit_code == 0, first.output
    assert _runs(project / "testo_history.db") == 1

    report.write_text(_junit(["test_a"], ["test_b"]), encoding="utf-8")
    result = CliRunner().invoke(app, ["try", "--repo", str(project)])

    assert result.exit_code == 0, result.output
    assert "<!-- testo-summary:shop -->" in result.stdout
    assert "test_b" in result.stdout
    assert "No test changed outcome" not in result.stdout  # test_b regressed vs run 1
    assert "compared with your local history" in result.output
    assert _runs(project / "testo_history.db") == 1  # still only the real run


def test_preview_writes_to_a_file(project: Path, tmp_path: Path) -> None:
    (project / "reports" / "junit.xml").write_text(_junit(["test_a"], []), encoding="utf-8")
    out = tmp_path / "comment.md"
    result = CliRunner().invoke(app, ["try", "--repo", str(project), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8").startswith("<!-- testo-summary:shop -->")
    assert not (project / "testo_history.db").exists()


def test_preview_without_results_says_how_to_get_them(project: Path) -> None:
    result = CliRunner().invoke(app, ["try", "--repo", str(project)])
    assert result.exit_code == 2
    assert "No result files found" in result.output


def test_from_github_needs_the_gh_cli(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(try_cmd.shutil, "which", lambda _name: None)
    result = CliRunner().invoke(app, ["try", "--repo", str(project), "--from-github"])
    assert result.exit_code == 2
    assert "GitHub CLI" in result.output


def test_run_flag_runs_the_detected_tests_first(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shlex
    import sys

    from qasight.services import ci_templates

    (project / "tests").mkdir()
    (project / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    (project / "pyproject.toml").write_text('[project]\nname = "shop"\n')
    out = project / "test-results" / "junit.xml"
    writer = (
        "import pathlib; p = pathlib.Path({!r}); p.parent.mkdir(parents=True, exist_ok=True); "
        "p.write_text({!r})".format(str(out), _junit(["test_a"], ["test_b"]))
    )
    monkeypatch.setattr(
        ci_templates,
        "ci_test_command",
        lambda stack: f"{shlex.quote(sys.executable)} -c {shlex.quote(writer)}",
    )

    result = CliRunner().invoke(app, ["try", "--run", "--repo", str(project)])

    assert result.exit_code == 0, result.output
    assert "Running pytest" in result.output
    assert "test_b" in result.stdout
    assert not (project / "testo_history.db").exists() or _runs(project / "testo_history.db") == 0


def test_run_flag_refuses_result_files(project: Path) -> None:
    result = CliRunner().invoke(app, ["try", "--run", "x.xml", "--repo", str(project)])

    assert result.exit_code == 2  # a usage error; the message is drawn in a Rich panel
