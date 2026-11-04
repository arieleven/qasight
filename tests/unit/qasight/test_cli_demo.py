"""``qasight demo`` (qasight/cli/commands/demo.py): the sample runs and where they are recorded."""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.cli.commands import demo as demo_mod


def _outcomes(run: int) -> dict[str, str]:
    out = {}
    for case in demo_mod.demo_cases(run):
        out[case.name] = "skipped" if case.skipped else ("failed" if case.failure else "passed")
    return out


def test_the_runs_tell_the_story_the_command_prints() -> None:
    runs = {n: _outcomes(n) for n in range(1, demo_mod.DEMO_RUNS + 1)}
    coupon = [runs[n]["test_apply_coupon"] for n in sorted(runs)]
    assert coupon == [
        "passed",
        "failed",
        "passed",
        "passed",
        "failed",
        "passed",
        "failed",
        "passed",
    ]
    assert [n for n in runs if runs[n]["test_redirect_after_login"] == "failed"] == [8]
    assert [n for n in runs if runs[n]["test_badge_count"] == "failed"] == [1, 2, 3]
    slow = {c.name: c.seconds for c in demo_mod.demo_cases(8)}["test_results_are_ranked"]
    early = {c.name: c.seconds for c in demo_mod.demo_cases(1)}["test_results_are_ranked"]
    assert slow > 1.5 > 0.5 > early
    assert demo_mod.demo_cases(4) == demo_mod.demo_cases(4)  # deterministic


def test_the_xml_counts_match_its_cases() -> None:
    suite = ET.fromstring(demo_mod.demo_junit_xml(2)).find("testsuite")
    assert suite is not None
    cases = suite.findall("testcase")
    assert int(suite.get("tests", "0")) == len(cases) == len(demo_mod.demo_cases(2))
    assert int(suite.get("failures", "0")) == sum(1 for c in cases if c.find("failure") is not None)
    assert int(suite.get("skipped", "0")) == sum(1 for c in cases if c.find("skipped") is not None)


def test_demo_records_eight_runs_in_its_own_folder(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("DATABASE_URL", raising=False)
    target = tmp_path / "demo"
    result = CliRunner().invoke(app, ["demo", "--dir", str(target), "--no-ui"])
    assert result.exit_code == 0, result.output
    assert "run 8/8 recorded" in result.output
    db = target / "testo_history.db"
    assert db.is_file()
    with sqlite3.connect(db) as conn:
        (count,) = conn.execute("select count(*) from runrecord").fetchone()
    assert count == demo_mod.DEMO_RUNS


def test_demo_refuses_a_folder_that_is_not_empty(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("mine", encoding="utf-8")
    result = CliRunner().invoke(app, ["demo", "--dir", str(tmp_path), "--no-ui"])
    assert result.exit_code == 2
    assert "not empty" in result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]
