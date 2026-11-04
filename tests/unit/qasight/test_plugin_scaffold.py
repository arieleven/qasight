"""``qasight plugins new`` (qasight/services/plugin_scaffold.py) writes a plugin that works."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.reporting.reporters.base import BaseReporter
from qasight.services.plugin_scaffold import ScaffoldError, names_for

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def test_names_follow_the_plugin_name() -> None:
    names = names_for("My-Runner")
    assert (names.name, names.dist, names.module, names.cls) == (
        "my-runner",
        "testo-my-runner-plugin",
        "testo_my_runner_plugin",
        "MyRunner",
    )
    for bad in ("1abc", "a b", "a--b", "a-", "", "ä"):
        with pytest.raises(ScaffoldError):
            names_for(bad)


def test_the_written_plugin_registers_loads_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = CliRunner().invoke(app, ["plugins", "new", "my-runner", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    root = tmp_path / "testo-my-runner-plugin"

    meta = tomllib.loads((root / "pyproject.toml").read_text())
    eps = meta["project"]["entry-points"]
    assert eps["qasight.frameworks"] == {"my-runner": "testo_my_runner_plugin:MyRunnerAdapter"}
    assert eps["qasight.reporters"] == {
        "my-runner-summary": "testo_my_runner_plugin:MyRunnerSummaryReporter"
    }
    config = yaml.safe_load((root / "qasight.yaml").read_text())
    assert config["cycles"]["unit"]["stages"][0]["equipment"] == "my-runner"
    assert (root / ".gitignore").exists()
    assert not list(root.rglob("*.tmpl"))

    monkeypatch.syspath_prepend(str(root))
    module = importlib.import_module("testo_my_runner_plugin")
    adapter = module.MyRunnerAdapter()
    argv = adapter.build_argv(
        target_repo=root,
        results_dir=tmp_path / "res",
        stage_args=("tests.test_example",),
        workers=1,
    )
    assert issubclass(module.MyRunnerSummaryReporter, BaseReporter)

    # The runner it calls writes one Allure result per test.
    proc = subprocess.run(
        argv,
        cwd=root,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(root), "PATH": ""},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    statuses = sorted(
        json.loads(p.read_text())["status"] for p in (tmp_path / "res").glob("*-result.json")
    )
    assert statuses in (["passed", "passed"], ["passed", "skipped"])
    assert argv[0] == sys.executable


def test_an_existing_folder_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "testo-x-plugin").mkdir()
    result = CliRunner().invoke(app, ["plugins", "new", "x", "--dir", str(tmp_path)])
    assert result.exit_code == 2
    assert list((tmp_path / "testo-x-plugin").iterdir()) == []


def test_plugins_without_a_subcommand_still_lists() -> None:
    result = CliRunner().invoke(app, ["plugins", "--json"])
    assert result.exit_code == 0
    assert any(row["name"] == "pytest" for row in json.loads(result.output))
