"""Run a chosen set of tests (2026-11-02): ``qasight run --test`` and ``--rerun-failed``."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.schema import Plan, Stage
from qasight.services import test_selection
from qasight.services.test_selection import SelectedTest, Selection, SelectionError

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _stage(name: str, framework: str, *args: str) -> Stage:
    return Stage(name=name, framework=framework, target_repo=Path("."), args=tuple(args))


def _plan(*stages: Stage) -> Plan:
    return Plan(name="c", description=None, stages=stages)


def test_pytest_gets_node_ids_when_the_stage_names_no_path(tmp_path: Path) -> None:
    selection = Selection(
        tests=(SelectedTest(name="test_b", full_name="tests.test_m.test_b", group="tests.test_m"),)
    )
    plan = test_selection.narrow_plan(
        _plan(_stage("unit", "pytest", "-q")), selection, scratch=tmp_path
    )
    (stage,) = plan.stages
    assert stage.args == ("-q", "tests/test_m.py::test_b")
    env = dict(stage.extra_env)
    assert env["TESTO_SELECTED_TESTS"] == "tests.test_m.test_b"
    assert json.loads(Path(env["ALLURE_TESTPLAN_PATH"]).read_text()) == {
        "version": "1.0",
        "tests": [{"selector": "tests.test_m.test_b"}],
    }


def test_pytest_falls_back_to_k_when_the_stage_names_a_path(tmp_path: Path) -> None:
    selection = test_selection.from_names(["test_a", "tests/test_m.py::test_b[2]"])
    plan = test_selection.narrow_plan(
        _plan(_stage("unit", "pytest", "-q", "tests")), selection, scratch=tmp_path
    )
    assert plan.stages[0].args == ("-q", "tests", "-k", "test_a or test_b")


def test_playwright_and_behave_get_their_own_filters(tmp_path: Path) -> None:
    selection = test_selection.from_names(["logs in (fast)", "checks out"])
    plan = test_selection.narrow_plan(
        _plan(_stage("e2e", "playwright"), _stage("bdd", "behave", "features/x.feature")),
        selection,
        scratch=tmp_path,
    )
    e2e, bdd = plan.stages
    assert e2e.args == ("--grep", r"logs in \(fast\)|checks out", "--pass-with-no-tests")
    assert bdd.args == ("features/x.feature", "--name", r"^(logs in \(fast\)|checks out)$")


def test_other_stages_run_unchanged_with_the_plan_file(tmp_path: Path) -> None:
    plan = test_selection.narrow_plan(
        _plan(_stage("java", "command", "mvn", "test")),
        test_selection.from_names(["com.acme.CartTest.adds"]),
        scratch=tmp_path,
    )
    (stage,) = plan.stages
    assert stage.args == ("mvn", "test")
    assert "ALLURE_TESTPLAN_PATH" in dict(stage.extra_env)


def test_a_selection_with_stages_keeps_only_those_stages(tmp_path: Path) -> None:
    selection = Selection(tests=(SelectedTest(name="test_x", stage="api"),))
    plan = test_selection.narrow_plan(
        _plan(_stage("unit", "pytest"), _stage("api", "pytest")), selection, scratch=tmp_path
    )
    assert [s.name for s in plan.stages] == ["api"]
    with pytest.raises(SelectionError, match="none of the chosen tests"):
        test_selection.narrow_plan(
            _plan(_stage("unit", "pytest")),
            Selection(tests=(SelectedTest(name="t", stage="gone"),)),
            scratch=tmp_path,
        )


def test_from_names_rejects_nothing_and_too_many() -> None:
    with pytest.raises(SelectionError):
        test_selection.from_names([" ", ""])
    with pytest.raises(SelectionError, match="too many"):
        test_selection.from_names([f"t{i}" for i in range(test_selection.MAX_TESTS + 1)])


def test_from_run_failures_needs_a_run_with_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from qasight.services import run_failures

    monkeypatch.setattr(run_failures, "load_run_failures", lambda run_id: None)
    with pytest.raises(SelectionError, match="no run"):
        test_selection.from_run_failures("nope")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text(
        "def test_ok():\n    assert True\n\n\ndef test_bad():\n    assert False\n", encoding="utf-8"
    )
    (tmp_path / "qasight.yaml").write_text(
        "version: 1\n"
        "defaults:\n  target_repo: .\n  artifacts_root: artifacts\n  workers: 1\n"
        "cycles:\n  c:\n    stages:\n      - name: unit\n        equipment: pytest\n"
        "        args: [-q, -p, no:cacheprovider]\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    # The stage's pytest must be this interpreter's (the venv's), not one on PATH.
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def test_cli_runs_only_the_chosen_test(repo: Path) -> None:
    runner = CliRunner()
    whole = runner.invoke(app, ["run", "--cycle", "c", "--no-persist", "--ci"])
    assert whole.exit_code == 1, whole.output
    chosen = runner.invoke(
        app, ["run", "--cycle", "c", "--no-persist", "--ci", "--test", "test_ok"]
    )
    assert chosen.exit_code == 0, chosen.output
    assert '"event":"selection"' in chosen.output.replace(" ", "")
    # None of the chosen tests in the stage: pytest's "nothing collected" is not a failure.
    missing = runner.invoke(
        app, ["run", "--cycle", "c", "--no-persist", "--ci", "--test", "test_gone"]
    )
    assert missing.exit_code == 0, missing.output


def test_cli_rejects_a_selection_with_cycle_all(repo: Path) -> None:
    result = CliRunner().invoke(app, ["run", "--cycle", "all", "--no-persist", "--test", "test_ok"])
    assert result.exit_code == 2
    assert "need one cycle" in result.output
