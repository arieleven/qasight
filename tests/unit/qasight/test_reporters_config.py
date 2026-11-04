"""Tests for `reporters:` parsing in qasight.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.loader import load_config
from qasight.config.schema import ReporterSpec


def _write_minimal_cycle_yaml(
    path: Path, *, top_extra: str = "", cycle_name: str = "my-cycle"
) -> None:
    body = f"""
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
{top_extra}
cycles:
  {cycle_name}:
    description: d
    stages:
      - name: s1
        equipment: pytest
        args: []
"""
    path.write_text(body, encoding="utf-8")


def test_reporters_block_parses_into_reporter_specs(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(
        yml,
        top_extra="""
reporters:
  - type: allure
  - type: testbeats
    slack_webhook: https://hooks.slack.example/xyz
""",
    )
    cfg = load_config(yml)
    assert cfg.reporters == (
        ReporterSpec(type="allure", options=()),
        ReporterSpec(
            type="testbeats", options=(("slack_webhook", "https://hooks.slack.example/xyz"),)
        ),
    )


def test_reporters_key_absent_defaults_to_empty_tuple(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(yml)
    cfg = load_config(yml)
    assert cfg.reporters == ()


def test_reporters_unsupported_type_raises(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(
        yml,
        top_extra="""
reporters:
  - type: bogus
""",
    )
    with pytest.raises(ConfigValidationError, match="unsupported type"):
        load_config(yml)


def test_reporters_not_a_list_raises(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(
        yml,
        top_extra="""
reporters: not-a-list
""",
    )
    with pytest.raises(ConfigValidationError, match="must be a list"):
        load_config(yml)
