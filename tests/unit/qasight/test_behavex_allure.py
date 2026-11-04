"""Tests for :mod:`qasight.reporting.behavex_allure`.

The end-to-end path (real ``behavex``, both reports) is covered by
``tests/integration/qasight/engine/test_behavex_reports.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.reporting.behavex_allure import export_behavex_allure_results

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def test_nothing_to_export_without_report_json(tmp_path: Path) -> None:
    results_dir = tmp_path / "allure-results" / "behavex"

    assert export_behavex_allure_results(tmp_path / "behave_reports", results_dir) is None
    assert not results_dir.exists()


def test_existing_results_are_left_alone(tmp_path: Path) -> None:
    """A stage that passed its own ``--formatter`` already wrote Allure results."""
    report_dir = tmp_path / "behave_reports"
    report_dir.mkdir()
    (report_dir / "report.json").write_text('{"features": []}', encoding="utf-8")
    results_dir = tmp_path / "allure-results" / "behavex"
    results_dir.mkdir(parents=True)
    (results_dir / "abc-result.json").write_text("{}", encoding="utf-8")

    assert export_behavex_allure_results(report_dir, results_dir) is None
    assert [p.name for p in results_dir.iterdir()] == ["abc-result.json"]


def test_unreadable_report_raises(tmp_path: Path) -> None:
    report_dir = tmp_path / "behave_reports"
    report_dir.mkdir()
    (report_dir / "report.json").write_text("{truncated", encoding="utf-8")

    with pytest.raises(ValueError):
        export_behavex_allure_results(report_dir, tmp_path / "allure-results" / "behavex")


@pytest.fixture
def fake_behavex(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stand-ins for BehaveX's ConfigRun and AllureBehaveXFormatter.

    The real modules need the ``bdd`` extra; the integration test runs them.
    These record what the formatter saw so the config swap can be checked.
    """
    import sys
    import types

    state: dict[str, object] = {"environ": {"output": "orig-out", "keep": "1"}, "calls": []}

    class ConfigRun:
        def __init__(self) -> None:
            self.environ = state["environ"]

    class AllureBehaveXFormatter:
        def launch_json_formatter(self, payload: dict) -> None:
            env = state["environ"]
            state["calls"].append((payload, dict(env)))  # type: ignore[union-attr]
            if state.get("fail"):
                raise RuntimeError("formatter broke")

    conf_mgr = types.ModuleType("behavex.conf_mgr")
    conf_mgr.ConfigRun = ConfigRun  # type: ignore[attr-defined]
    formatter = types.ModuleType("behavex.outputs.formatters.allure_behavex_formatter")
    formatter.AllureBehaveXFormatter = AllureBehaveXFormatter  # type: ignore[attr-defined]
    for name, module in {
        "behavex": types.ModuleType("behavex"),
        "behavex.conf_mgr": conf_mgr,
        "behavex.outputs": types.ModuleType("behavex.outputs"),
        "behavex.outputs.formatters": types.ModuleType("behavex.outputs.formatters"),
        "behavex.outputs.formatters.allure_behavex_formatter": formatter,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return state


def _report(tmp_path: Path, payload: str) -> Path:
    report_dir = tmp_path / "behave_reports"
    (report_dir / "outputs" / "logs" / "feature_a").mkdir(parents=True)
    (report_dir / "outputs" / "logs" / "feature_a" / "scenario.log").write_text("log", "utf-8")
    (report_dir / "report.json").write_text(payload, encoding="utf-8")
    return report_dir


def test_export_runs_the_formatter_with_swapped_dirs_and_counts_scenarios(
    tmp_path: Path, fake_behavex: dict[str, object]
) -> None:
    report_dir = _report(
        tmp_path, '{"features": [{"scenarios": [{}, {}]}, {"scenarios": [{}]}, {}]}'
    )
    results_dir = tmp_path / "allure-results" / "behavex"

    assert export_behavex_allure_results(report_dir, results_dir) == 3

    # Logs are copied in first, so the formatter's relative links resolve.
    assert (results_dir / "feature_a" / "scenario.log").read_text("utf-8") == "log"
    [(payload, env_during)] = fake_behavex["calls"]  # type: ignore[misc]
    assert len(payload["features"]) == 3
    assert env_during["output"] == str(report_dir.resolve())
    assert env_during["logs"] == str(results_dir.resolve())
    # BehaveX's process-wide config is put back afterwards.
    assert fake_behavex["environ"] == {"output": "orig-out", "keep": "1"}


def test_config_is_restored_when_the_formatter_fails(
    tmp_path: Path, fake_behavex: dict[str, object]
) -> None:
    fake_behavex["fail"] = True
    report_dir = _report(tmp_path, '{"features": []}')

    with pytest.raises(RuntimeError, match="formatter broke"):
        export_behavex_allure_results(report_dir, tmp_path / "allure-results" / "behavex")

    assert fake_behavex["environ"] == {"output": "orig-out", "keep": "1"}
