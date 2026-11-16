"""``qasight case-study``: before/after numbers for a beta team."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.repository.db_config import reset_engine_cache

_FAIL = (
    b'<testsuite name="s"><testcase classname="c" name="t1"><failure message="AssertionError: 3 != 4"/>'
    b'</testcase><testcase classname="c" name="t2"/></testsuite>'
)
_PASS = b'<testsuite name="s"><testcase classname="c" name="t1"/><testcase classname="c" name="t2"/></testsuite>'


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'h.db'}")
    monkeypatch.setenv("TESTO_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    reset_engine_cache()
    yield tmp_path
    reset_engine_cache()


def _record(xml: bytes) -> str:
    from qasight.config.loader import load_config_or_defaults
    from qasight.services.junit_ingest import ingest_uploaded_reports

    out = ingest_uploaded_reports(
        cfg=load_config_or_defaults(), cycle="shop", reports=[("r.xml", xml)]
    )
    assert out.run_id
    return out.run_id


def test_numbers_after_the_switch(history: Path) -> None:
    from qasight.services.case_study import build, markdown
    from qasight.services.triage import TriageStore

    failing = _record(_FAIL)
    _record(_PASS)
    TriageStore().mark(
        cycle="shop", message="AssertionError: 3 != 4", cause="product_bug", run_id=failing
    )
    since = datetime.now(tz=UTC) - timedelta(days=1)
    study = build(["shop"], since=since)
    assert study.after.runs == 2 and study.after.results == 4
    assert study.after.failed_runs_pct == 50.0
    assert study.before.runs == 0 and study.notes
    assert study.causes_marked == 1 and study.median_hours_to_cause is not None
    text = markdown(study)
    assert "| Runs that failed | - | 50.0% |" in text


def test_cli_prints_markdown_and_checks_the_date(history: Path) -> None:
    from qasight.cli.app import app

    runner = CliRunner()
    bad = runner.invoke(app, ["case-study", "--cycle", "shop", "--since", "yesterday"])
    assert bad.exit_code == 2
    ok = runner.invoke(app, ["case-study", "--cycle", "shop", "--since", "2026-01-01"])
    assert ok.exit_code == 0, ok.output
    assert "before and after 2026-01-01" in ok.output
