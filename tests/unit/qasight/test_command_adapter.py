"""``command`` framework: config parsing, the adapter, JUnit → Allure import,
and a real end-to-end stage (a subprocess that writes JUnit XML)."""

from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.loader import load_config
from qasight.config.schema import Stage
from qasight.engine.executor import run_stage
from qasight.frameworks.base import get_adapter
from qasight.frameworks.command_adapter import CommandAdapter
from qasight.reporting.junit_import import import_junit_reports

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

JUNIT = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <testsuites name="jest tests" tests="4">
      <testsuite name="payout" timestamp="2026-09-25T10:00:00" tests="4">
        <testcase classname="payout rules" name="masks numbers" time="0.012"/>
        <testcase classname="payout rules" name="rejects masked resend" time="0.5">
          <failure message="expected 400">AssertionError: expected 400
      at Object.&lt;anonymous&gt; (payout.test.ts:10:5)</failure>
        </testcase>
        <testcase classname="payout rules" name="kms down" time="0.1"><error>ECONNRESET</error></testcase>
        <testcase classname="payout rules" name="bit later" time="0"><skipped/></testcase>
      </testsuite>
    </testsuites>
    """
)


def _write_config(tmp_path: Path, stage_yaml: str) -> Path:
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(
        "version: 1\ncycles:\n  app:\n    stages:\n" + textwrap.indent(stage_yaml, "      "),
        encoding="utf-8",
    )
    return cfg


def _results(results_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(results_dir.glob("*-result.json"))]


# --- config ----------------------------------------------------------------------


def test_config_accepts_command_stage_with_junit(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        "- name: jest\n  framework: command\n  args: npx jest --ci\n"
        "  junit_xml: [reports/junit.xml, 'e2e/**/*.xml']\n",
    )
    stage = load_config(cfg).cycles["app"].stages[0]
    assert stage.framework == "command"
    assert stage.args == ("npx", "jest", "--ci")
    assert stage.junit_xml == ("reports/junit.xml", "e2e/**/*.xml")
    assert stage.tier == "unit"


def test_config_single_junit_string_and_other_frameworks(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, "- name: py\n  framework: pytest\n  junit_xml: out/junit.xml\n")
    assert load_config(cfg).cycles["app"].stages[0].junit_xml == ("out/junit.xml",)


@pytest.mark.parametrize(
    ("stage_yaml", "match"),
    [
        ("- name: bad\n  framework: command\n", "needs 'args'"),
        ("- name: bad\n  framework: command\n  args: x\n  junit_xml: []\n", "junit_xml"),
        ("- name: bad\n  framework: command\n  args: x\n  junit_xml: [3]\n", "junit_xml"),
        ("- name: bad\n  framework: command\n  args: x\n  junit_xml: /etc/junit.xml\n", "relative"),
        ("- name: bad\n  framework: command\n  args: x\n  junit_xml: ../outside.xml\n", "inside"),
    ],
)
def test_config_rejects_bad_command_stages(tmp_path: Path, stage_yaml: str, match: str) -> None:
    with pytest.raises(ConfigValidationError, match=match):
        load_config(_write_config(tmp_path, stage_yaml))


# --- adapter ----------------------------------------------------------------------


def test_adapter_runs_args_verbatim(tmp_path: Path) -> None:
    adapter = get_adapter("command")
    assert isinstance(adapter, CommandAdapter)
    assert adapter.results_subdir() == "command"
    argv = adapter.build_argv(
        target_repo=tmp_path,
        results_dir=tmp_path / "r",
        stage_args=("npx", "jest", "--ci"),
        workers=8,
    )
    assert argv == ["npx", "jest", "--ci"]
    assert adapter.native_report(tmp_path) is None
    with pytest.raises(ValueError, match="needs args"):
        adapter.build_argv(target_repo=tmp_path, results_dir=tmp_path, stage_args=(), workers=1)


# --- JUnit import -------------------------------------------------------------------


def test_junit_cases_become_allure_results(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "junit.xml").write_text(JUNIT, encoding="utf-8")
    out = tmp_path / "results"

    imported = import_junit_reports(
        target_repo=tmp_path, patterns=("reports/*.xml",), results_dir=out, tool="jest"
    )

    assert imported.tests == 4 and imported.errors == ()
    by_name = {r["name"]: r for r in _results(out)}
    assert {n: r["status"] for n, r in by_name.items()} == {
        "masks numbers": "passed",
        "rejects masked resend": "failed",
        "kms down": "broken",
        "bit later": "skipped",
    }
    failed = by_name["rejects masked resend"]
    assert failed["fullName"] == "payout rules.rejects masked resend"
    assert failed["statusDetails"]["message"] == "expected 400"
    assert "payout.test.ts:10:5" in failed["statusDetails"]["trace"]
    assert by_name["kms down"]["statusDetails"]["message"] == "ECONNRESET"
    assert failed["stop"] - failed["start"] == 500
    # sequential timing within the suite
    assert by_name["rejects masked resend"]["start"] == by_name["masks numbers"]["stop"]
    labels = {lbl["name"]: lbl["value"] for lbl in failed["labels"]}
    assert labels == {
        "suite": "payout",
        "testClass": "payout rules",
        "framework": "jest",
        "language": "junit",
    }
    # stable history id per test, so trends line up across runs
    again = tmp_path / "again"
    import_junit_reports(
        target_repo=tmp_path, patterns=("reports/*.xml",), results_dir=again, tool="jest"
    )
    assert {r["historyId"] for r in _results(again)} == {r["historyId"] for r in _results(out)}


def test_bare_testsuite_root_without_timestamp(tmp_path: Path) -> None:
    (tmp_path / "one.xml").write_text(
        '<testsuite name="s"><testcase name="t" time="bad"/></testsuite>', encoding="utf-8"
    )
    imported = import_junit_reports(
        target_repo=tmp_path, patterns=("*.xml",), results_dir=tmp_path / "r"
    )
    (result,) = _results(tmp_path / "r")
    assert imported.tests == 1
    assert result["status"] == "passed" and result["stop"] == result["start"]
    assert result["fullName"] == "s.t"


def test_malformed_stale_and_escaping_files_are_skipped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "broken.xml").write_text("<testsuite><testcase", encoding="utf-8")
    stale = repo / "stale.xml"
    stale.write_text('<testsuite name="old"><testcase name="t"/></testsuite>', encoding="utf-8")
    old = time.time() - 3600
    import os

    os.utime(stale, (old, old))
    (tmp_path / "outside.xml").write_text(
        '<testsuite><testcase name="x"/></testsuite>', encoding="utf-8"
    )
    (repo / "link.xml").symlink_to(tmp_path / "outside.xml")

    imported = import_junit_reports(
        target_repo=repo,
        patterns=("*.xml",),
        results_dir=tmp_path / "r",
        not_before=time.time() - 60,
    )
    assert imported.tests == 0
    assert [e.split(":")[0] for e in imported.errors] == ["broken.xml"]
    assert _results(tmp_path / "r") == []


# --- end to end through run_stage ----------------------------------------------------


def _script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "runner.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def test_command_stage_runs_and_imports_its_junit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = _script(
        tmp_path,
        f"""
        import pathlib, sys
        pathlib.Path("reports").mkdir(exist_ok=True)
        pathlib.Path("reports/junit.xml").write_text({JUNIT!r})
        print("ran with", sys.argv[1:])
        sys.exit(1)
        """,
    )
    stage = Stage(
        name="jest",
        framework="command",
        target_repo=repo,
        args=(sys.executable, str(script), "--ci"),
        junit_xml=("reports/junit.xml",),
    )

    result = run_stage(stage, plan_name="app", artifacts_root=tmp_path / "artifacts")

    assert result.returncode == 1
    results_dir = tmp_path / "artifacts" / "app" / "jest" / "allure-results" / "command"
    assert len(_results(results_dir)) == 4
    log = (tmp_path / "artifacts" / "app" / "jest" / "run.log").read_text()
    assert "ran with ['--ci']" in log
    assert "[qasight] junit_xml: imported 4 test(s) from 1 file(s)" in log


def test_missing_report_is_logged_not_raised(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    stage = Stage(
        name="e2e",
        framework="command",
        target_repo=repo,
        args=(sys.executable, "-c", "print('no report')"),
        junit_xml=("test-results/*.xml",),
    )
    result = run_stage(stage, plan_name="app", artifacts_root=tmp_path / "a")
    assert result.returncode == 0
    log = (tmp_path / "a" / "app" / "e2e" / "run.log").read_text()
    assert "imported 0 test(s) from 0 file(s)" in log


def test_import_crash_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import qasight.reporting.junit_import as junit_import

    def boom(**_: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(junit_import, "import_junit_reports", boom)
    repo = tmp_path / "repo"
    repo.mkdir()
    stage = Stage(
        name="s",
        framework="command",
        target_repo=repo,
        args=(sys.executable, "-c", "pass"),
        junit_xml=("*.xml",),
    )
    run_stage(stage, plan_name="p", artifacts_root=tmp_path / "a")
    assert "import failed: disk full" in (tmp_path / "a" / "p" / "s" / "run.log").read_text()
