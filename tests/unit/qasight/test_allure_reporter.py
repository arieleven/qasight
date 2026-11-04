"""AllureReporter — merged vs. per-framework output selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from qasight.reporting.allure import AllureGenerateResult
from qasight.reporting.collector import CollectedResults, StageCollection
from qasight.reporting.reporters.allure_reporter import AllureReporter
from qasight.reporting.reporters.base import ReportContext


def _fake_results(tmp_path: Path) -> CollectedResults:
    stages = []
    for framework in ("pytest", "behave", "behavex"):
        d = tmp_path / framework
        d.mkdir(parents=True)
        stages.append(
            StageCollection(
                plan="p",
                stage=f"{framework}-stage",
                framework=framework,
                results_dir=d,
                log_path=None,
            )
        )
    return CollectedResults(artifacts_root=tmp_path, stages=stages)


def test_publish_generates_one_report_per_framework_when_run_report_root_set(
    tmp_path: Path,
) -> None:
    results = _fake_results(tmp_path)
    run_report_root = tmp_path / "history" / "rid-1"
    context = ReportContext(
        artifacts_root=tmp_path,
        run_id="rid-1",
        generate_only=True,
        run_report_root=run_report_root,
    )

    calls: list[Path] = []

    def _fake_generate_html(*, result_dirs, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text("<html></html>", encoding="utf-8")
        calls.append(out_dir)
        return AllureGenerateResult(ok=True, out_dir=out_dir, message="ok")

    with patch("qasight.reporting.allure.generate_html", side_effect=_fake_generate_html):
        outcome = AllureReporter().publish(results=results, context=context)

    assert outcome.ok
    assert len(calls) == 3
    generated = {c.name: c for c in calls}
    assert set(generated) == {"pytest", "behave", "behavex"}
    for framework, out_dir in generated.items():
        assert out_dir == run_report_root / "allure_reports" / framework
    assert len(outcome.artifacts) == 3


def test_publish_generates_single_merged_report_when_no_run_report_root(tmp_path: Path) -> None:
    results = _fake_results(tmp_path)
    out_dir = tmp_path / "allure-report"
    context = ReportContext(artifacts_root=tmp_path, generate_only=True, out_dir=out_dir)

    calls: list[list[Path]] = []

    def _fake_generate_html(*, result_dirs, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text("<html></html>", encoding="utf-8")
        calls.append(list(result_dirs))
        return AllureGenerateResult(ok=True, out_dir=out_dir, message="ok")

    with patch("qasight.reporting.allure.generate_html", side_effect=_fake_generate_html):
        outcome = AllureReporter().publish(results=results, context=context)

    assert outcome.ok
    assert len(calls) == 1
    assert len(calls[0]) == 3
    assert outcome.artifacts == (out_dir / "index.html",)
