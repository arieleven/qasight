from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from qasight.engine.exit_codes import EngineExitCode
from qasight.reporting.allure import AllureGenerateResult
from qasight.reporting.collector import CollectedResults, StageCollection
from qasight.reporting.entry import dispatch_report
from qasight.reporting.paths import (
    discover_latest_plan_dir,
    plan_artifacts_dir,
    relpath_for_display,
)


def test_relpath_for_display_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "artifacts" / "report" / "index.html"
    p.parent.mkdir(parents=True)
    p.write_text("x", encoding="utf-8")
    rel = relpath_for_display(p)
    assert rel.startswith("./")
    assert "artifacts/report/index.html" in rel.replace("\\", "/")


def test_plan_artifacts_dir_rejects_paths_outside_artifacts_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    with pytest.raises(ValueError, match="plan name escapes root"):
        plan_artifacts_dir(root, "../outside")
    with pytest.raises(ValueError, match="plan name escapes root"):
        plan_artifacts_dir(root, str(tmp_path / "outside"))


def test_discover_latest_plan_dir_by_events_mtime(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    older = art / "older"
    newer = art / "newer"
    older.mkdir()
    newer.mkdir()
    (older / "events.ndjson").write_text("{}", encoding="utf-8")
    (newer / "events.ndjson").write_text("{}", encoding="utf-8")
    time.sleep(0.05)
    (newer / "events.ndjson").write_text("[]", encoding="utf-8")
    picked = discover_latest_plan_dir(art)
    assert picked is not None
    assert picked.name == "newer"


def test_discover_latest_plan_dir_none_when_empty(tmp_path: Path) -> None:
    art = tmp_path / "empty"
    art.mkdir()
    assert discover_latest_plan_dir(art) is None


def test_collect_results_uses_latest_when_no_plan_name(tmp_path: Path) -> None:
    from qasight.reporting.collector import collect_results

    art = tmp_path / "artifacts"
    art.mkdir()
    stale = art / "stale"
    fresh = art / "fresh"
    stale.mkdir()
    fresh.mkdir()
    (stale / "events.ndjson").write_text("{}", encoding="utf-8")
    (fresh / "events.ndjson").write_text("{}", encoding="utf-8")

    st = stale / "unit-pytest" / "allure-results" / "pytest"
    st.mkdir(parents=True)
    (st / "x-result.json").write_text("{}", encoding="utf-8")

    fr = fresh / "unit-pytest" / "allure-results" / "pytest"
    fr.mkdir(parents=True)
    (fr / "y-result.json").write_text("{}", encoding="utf-8")

    time.sleep(0.05)
    (fresh / "events.ndjson").write_text("[]", encoding="utf-8")

    results = collect_results(art, plan_name=None)
    assert results.stages
    assert all(s.plan == "fresh" for s in results.stages)


def test_dispatch_report_generate_only_does_not_open_dashboard(tmp_path: Path) -> None:
    from rich.console import Console

    rd = tmp_path / "pytest_results"
    rd.mkdir(parents=True)
    fake = CollectedResults(
        artifacts_root=tmp_path,
        stages=[
            StageCollection(
                plan="unit",
                stage="pytest",
                framework="pytest",
                results_dir=rd,
                log_path=None,
            )
        ],
    )
    out = tmp_path / "report_out"
    out.mkdir()
    (out / "index.html").write_text("<html></html>", encoding="utf-8")
    console = Console(record=True, width=120)
    with (
        patch("qasight.reporting.entry.collect_results", return_value=fake),
        patch("qasight.reporting.allure.generate_html") as gen,
        patch("qasight.reporting.server.open_generated_report") as op,
    ):
        gen.return_value = AllureGenerateResult(ok=True, out_dir=out, message="ok")
        code = dispatch_report(
            console=console,
            artifacts_root=tmp_path,
            plan_name=None,
            generate_only=True,
            port=8765,
            host="127.0.0.1",
            out_dir=out,
            fmt="html",
            summary_out=None,
            inject_history=False,
        )
        assert code == int(EngineExitCode.SUCCESS)
        op.assert_not_called()
