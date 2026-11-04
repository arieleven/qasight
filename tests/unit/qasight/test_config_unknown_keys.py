"""Keys nothing reads are reported (P5 in docs/engine-cli-review.md)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.loader import load_config
from qasight.config.unknown_keys import unknown_keys

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

TYPOS = """\
version: 1
defaults:
  timeout: 5
cycles:
  web:
    triger: {paths: ["src/**"]}
    stages:
      - name: unit
        equipment: pytest
        env: {A: "1"}
        timout_s: 3
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "qasight.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def test_typos_are_named_with_a_guess(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, TYPOS))
    assert cfg.warnings == (
        "unknown key 'defaults.timeout' is ignored; did you mean 'timeout_s'?",
        "unknown key 'cycles.web.triger' is ignored; did you mean 'trigger'?",
        "unknown key 'cycles.web.stages[0].env' is ignored; did you mean 'extra_env'?",
        "unknown key 'cycles.web.stages[0].timout_s' is ignored; did you mean 'timeout_s'?",
    )


def test_open_maps_take_any_name() -> None:
    raw = {
        "version": 1,
        "cycles": {
            "any-name": {
                "stages": [{"name": "s", "equipment": "pytest", "extra_env": {"WHATEVER": "x"}}]
            }
        },
    }
    assert unknown_keys(raw) == []


def test_every_config_in_the_repo_is_clean() -> None:
    root = Path(__file__).resolve().parents[3]
    for path in [root / "qasight.yaml", *root.glob("examples/**/qasight.yaml")]:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
        if isinstance(raw, dict) and "cycles" in raw:
            assert unknown_keys(raw) == [], path


def test_validate_warns_and_strict_fails(tmp_path: Path) -> None:
    path = _write(tmp_path, TYPOS)
    runner = CliRunner()
    loose = runner.invoke(
        app, ["config", "validate", "--config", str(path), "--no-check-executables"]
    )
    assert loose.exit_code == 0
    assert "did you mean 'timeout_s'" in loose.output
    strict = runner.invoke(
        app, ["config", "validate", "--config", str(path), "--no-check-executables", "--strict"]
    )
    assert strict.exit_code == 2
