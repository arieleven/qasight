"""``snapshot_native_reports`` — copies each stage's native report (if any)."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.schema import Plan, Stage
from qasight.services.cycle_run import snapshot_native_reports


def _make_plan(*, name: str, stages: tuple[Stage, ...]) -> Plan:
    return Plan(name=name, description=None, stages=stages)


def test_copies_behavex_native_report_and_derives_index_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts_root = tmp_path / "artifacts"
    static_history = tmp_path / "static" / "history"
    monkeypatch.setattr("qasight.paths.STATIC_HISTORY_ROOT", static_history)

    plan = _make_plan(
        name="my-cycle",
        stages=(Stage(name="flow-tests", framework="behavex", target_repo=tmp_path),),
    )
    reports_dir = artifacts_root / "my-cycle" / "flow-tests" / "behave_reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "report.html").write_text("<html>native</html>", encoding="utf-8")
    (reports_dir / "overall_status.json").write_text("{}", encoding="utf-8")

    snapshot_native_reports(plan=plan, artifacts_root=artifacts_root, run_id="rid-1")

    dest = static_history / "rid-1" / "native_reports" / "behavex"
    assert (dest / "report.html").read_text(encoding="utf-8") == "<html>native</html>"
    assert (dest / "overall_status.json").is_file()
    assert (dest / "index.html").read_text(encoding="utf-8") == "<html>native</html>"


def test_pytest_and_behave_stages_produce_no_native_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts_root = tmp_path / "artifacts"
    static_history = tmp_path / "static" / "history"
    monkeypatch.setattr("qasight.paths.STATIC_HISTORY_ROOT", static_history)

    plan = _make_plan(
        name="my-cycle",
        stages=(
            Stage(name="pytest-sample", framework="pytest", target_repo=tmp_path),
            Stage(name="behave-features", framework="behave", target_repo=tmp_path),
        ),
    )
    (artifacts_root / "my-cycle" / "pytest-sample").mkdir(parents=True)
    (artifacts_root / "my-cycle" / "behave-features").mkdir(parents=True)

    snapshot_native_reports(plan=plan, artifacts_root=artifacts_root, run_id="rid-1")

    assert not (static_history / "rid-1" / "native_reports").exists()


def test_no_op_when_run_id_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    static_history = tmp_path / "static" / "history"
    monkeypatch.setattr("qasight.paths.STATIC_HISTORY_ROOT", static_history)

    plan = _make_plan(
        name="my-cycle",
        stages=(Stage(name="flow-tests", framework="behavex", target_repo=tmp_path),),
    )
    snapshot_native_reports(plan=plan, artifacts_root=tmp_path / "artifacts", run_id=None)

    assert not static_history.exists()
