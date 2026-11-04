"""timeout_s, workers and version are checked the way the JSON Schema describes them."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.loader import load_config


def _load(tmp_path: Path, stage_extra: str = "", top: str = "") -> object:
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(
        f"{top}cycles:\n  a: {{stages: [{{name: s, equipment: pytest{stage_extra}}}]}}\n",
        encoding="utf-8",
    )
    return load_config(cfg)


@pytest.mark.parametrize("value", ["abc", "-1", "0", "[1]", "true", ".inf"])
def test_bad_stage_timeout_is_a_config_error(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigValidationError, match="timeout_s"):
        _load(tmp_path, f", timeout_s: {value}")


@pytest.mark.parametrize(("value", "expected"), [("5", 5.0), ("0.5", 0.5), ("null", None)])
def test_good_stage_timeout(tmp_path: Path, value: str, expected: float | None) -> None:
    config = _load(tmp_path, f", timeout_s: {value}")
    assert config.cycles["a"].stages[0].timeout_s == expected  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["0", "-2", "x", "2.5", "true"])
def test_bad_stage_workers_is_a_config_error(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigValidationError, match="workers"):
        _load(tmp_path, f", workers: {value}")


def test_bad_defaults_numbers_are_config_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError, match="defaults: 'timeout_s'"):
        _load(tmp_path, top="defaults: {timeout_s: soon}\n")
    with pytest.raises(ConfigValidationError, match="defaults: 'workers'"):
        _load(tmp_path, top="defaults: {workers: 0}\n")


def test_good_workers(tmp_path: Path) -> None:
    config = _load(tmp_path, ", workers: 3")
    assert config.cycles["a"].stages[0].workers == 3  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["2", "99", "abc", "true"])
def test_unknown_version_is_a_config_error(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigValidationError, match="version"):
        _load(tmp_path, top=f"version: {value}\n")


@pytest.mark.parametrize("value", ["1", "'1'"])
def test_version_one_loads(tmp_path: Path, value: str) -> None:
    assert _load(tmp_path, top=f"version: {value}\n").version == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["0", "-3"])
def test_run_workers_flag_must_be_at_least_one(value: str) -> None:
    from typer.testing import CliRunner

    from qasight.cli.app import app

    result = CliRunner().invoke(app, ["run", "--cycle", "x", "--workers", value])
    assert result.exit_code == 2
