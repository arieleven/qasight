"""Code coverage on a run: reading the reports, storing the number, showing its change."""

from __future__ import annotations

import textwrap
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.engine.orchestrator import run_plan
from qasight.history.views import CompletedRunView
from qasight.reporting.coverage_import import (
    CoverageFormatError,
    clean_coverage_metadata,
    parse_coverage,
    parse_coverage_text,
)
from qasight.repository.models import RunStatus
from qasight.services.cycle_run import junit_ingest_plan
from qasight.services.junit_ingest import clean_run_metadata
from qasight.services.run_summary_markdown import coverage_text
from tests.fixtures.engine import NoopRenderer

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

COBERTURA = textwrap.dedent(
    """\
    <?xml version="1.0" ?>
    <!DOCTYPE coverage SYSTEM "http://cobertura.sourceforge.net/xml/coverage-04.dtd">
    <coverage version="7.6" lines-valid="200" lines-covered="150" line-rate="0.75">
      <packages/>
    </coverage>
    """
)
JACOCO = textwrap.dedent(
    """\
    <?xml version="1.0"?>
    <report name="shop">
      <package name="a"><counter type="LINE" missed="1" covered="1"/></package>
      <counter type="INSTRUCTION" missed="40" covered="60"/>
      <counter type="LINE" missed="20" covered="80"/>
    </report>
    """
)
LCOV = "TN:\nSF:src/a.ts\nDA:1,1\nLF:10\nLH:7\nend_of_record\nSF:src/b.ts\nLF:10\nLH:3\nend_of_record\n"
GO = textwrap.dedent(
    """\
    mode: set
    example.com/shop/cart.go:3.20,5.2 2 1
    example.com/shop/cart.go:7.20,9.2 3 0
    example.com/shop/cart.go:7.20,9.2 3 1
    example.com/shop/pay.go:1.10,2.2 5 0
    """
)
PASSING = '<testsuite name="s" tests="1"><testcase classname="s" name="ok" time="0.1"/></testsuite>'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (COBERTURA, ("cobertura", 150, 200)),
        (JACOCO, ("jacoco", 80, 100)),
        (LCOV, ("lcov", 10, 20)),
        # A block listed by two test binaries counts once, covered if either ran it.
        (GO, ("go", 5, 10)),
    ],
)
def test_each_format_is_told_by_its_content(text: str, expected: tuple[str, int, int]) -> None:
    assert parse_coverage_text(text) == expected


def test_reports_are_added_together(tmp_path: Path) -> None:
    a, b = tmp_path / "coverage.xml", tmp_path / "lcov.info"
    a.write_text(COBERTURA, encoding="utf-8")
    b.write_text(LCOV, encoding="utf-8")
    summary = parse_coverage([a, b])
    assert (summary.covered, summary.total, summary.formats) == (160, 220, ("cobertura", "lcov"))
    assert summary.as_metadata() == {
        "coverage_pct": "72.73",
        "coverage_covered": "160",
        "coverage_total": "220",
    }


def test_other_files_and_entities_are_refused(tmp_path: Path) -> None:
    with pytest.raises(CoverageFormatError):
        parse_coverage_text("<testsuite/>")
    with pytest.raises(CoverageFormatError):
        parse_coverage_text("just text")
    bomb = '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY a "aaaa">]><coverage>&a;</coverage>'
    with pytest.raises(CoverageFormatError):
        parse_coverage_text(bomb)


def test_uploaded_coverage_must_be_consistent_numbers() -> None:
    assert clean_coverage_metadata({"coverage_covered": "5", "coverage_total": "10"}) == {
        "coverage_pct": "50.00",
        "coverage_covered": "5",
        "coverage_total": "10",
    }
    assert clean_coverage_metadata({"coverage_covered": "11", "coverage_total": "10"}) == {}
    assert clean_coverage_metadata({"coverage_covered": "x", "coverage_total": "10"}) == {}
    # The percentage is always recomputed by the server, never taken from the client.
    cleaned = clean_run_metadata(
        {"coverage_pct": "99", "coverage_covered": "1", "coverage_total": "4", "ci_provider": "gh"}
    )
    assert cleaned == {
        "ci_provider": "gh",
        "coverage_pct": "25.00",
        "coverage_covered": "1",
        "coverage_total": "4",
    }


def test_run_plan_stores_coverage_with_the_run(tmp_path: Path) -> None:
    (tmp_path / "a.xml").write_text(PASSING, encoding="utf-8")
    (tmp_path / "coverage.xml").write_text(COBERTURA, encoding="utf-8")
    plan = junit_ingest_plan(cycle="web", reports=["a.xml"], target_repo=tmp_path)
    result = run_plan(
        plan,
        renderer=NoopRenderer(),
        artifacts_root=tmp_path / "out",
        persist=False,
        run_metadata={"ci_provider": "github"},
        coverage_reports=[tmp_path / "coverage.xml", tmp_path / "missing.info"],
    )
    assert result.extra["run_metadata"] == {
        "ci_provider": "github",
        "coverage_pct": "75.00",
        "coverage_covered": "150",
        "coverage_total": "200",
    }


def test_a_missing_report_does_not_fail_the_run(tmp_path: Path) -> None:
    (tmp_path / "a.xml").write_text(PASSING, encoding="utf-8")
    plan = junit_ingest_plan(cycle="web", reports=["a.xml"], target_repo=tmp_path)
    result = run_plan(
        plan,
        renderer=NoopRenderer(),
        artifacts_root=tmp_path / "out",
        persist=False,
        coverage_reports=[tmp_path / "missing.xml"],
    )
    assert int(result.exit_code) == 0
    assert "run_metadata" not in result.extra


def test_cli_ingest_checks_the_coverage_report_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.xml").write_text(PASSING, encoding="utf-8")
    (tmp_path / "bad.xml").write_text("<nope/>", encoding="utf-8")
    runner = CliRunner()
    missing = runner.invoke(
        app, ["ingest", "a.xml", "--cycle", "web", "--no-persist", "--coverage", "nope.xml"]
    )
    assert missing.exit_code == 2
    assert "no coverage report matches" in missing.output
    bad = runner.invoke(
        app, ["ingest", "a.xml", "--cycle", "web", "--no-persist", "--coverage", "bad.xml"]
    )
    assert bad.exit_code == 2
    assert "--coverage" in bad.output
    (tmp_path / "coverage.xml").write_text(COBERTURA, encoding="utf-8")
    ok = runner.invoke(
        app, ["ingest", "a.xml", "--cycle", "web", "--no-persist", "--coverage", "coverage.xml"]
    )
    assert ok.exit_code == 0, ok.output


def _run(coverage: float | None) -> CompletedRunView:
    return CompletedRunView(
        run_id="r",
        status=RunStatus.COMPLETED,
        created_at=1,
        started_at=1,
        finished_at=2,
        test_kind="cycle",
        returncode=0,
        wall_duration_ms=1.0,
        metrics_duration_ms=None,
        total_tests=1,
        passed=1,
        failed=0,
        broken=0,
        skipped=0,
        avg_case_ms=None,
        health_pct=100.0,
        target_repo=None,
        snapshot_dir=None,
        audit_json=None,
        coverage_pct=coverage,
    )


def test_pr_comment_shows_coverage_and_its_change() -> None:
    now = _run(81.24)
    assert coverage_text(_run(None), None) == ""
    assert coverage_text(now, None) == "coverage 81.2%"
    assert coverage_text(now, _run(None)) == "coverage 81.2%"
    assert coverage_text(now, _run(81.6)) == "coverage 81.2% (▼ 0.4)"
    assert coverage_text(now, _run(80.0)) == "coverage 81.2% (▲ 1.2)"
    assert coverage_text(now, replace(now, run_id="b")) == "coverage 81.2% (unchanged)"
