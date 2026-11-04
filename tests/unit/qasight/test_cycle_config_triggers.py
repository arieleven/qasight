"""Tests for cycle ``trigger`` parsing in qasight.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.loader import load_config
from qasight.config.schema import CycleTrigger


def _write_minimal_cycle_yaml(
    path: Path,
    *,
    cycle_extra: str = "",
    cycle_name: str = "my-cycle",
) -> None:
    body = f"""
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
cycles:
  {cycle_name}:
    description: d
{cycle_extra}
    stages:
      - name: s1
        equipment: pytest
        args: []
"""
    path.write_text(body, encoding="utf-8")


def test_trigger_paths_parsed(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(
        yml,
        cycle_extra="""
    trigger:
      paths:
        - "services/db/**"
        - "shared/**/*.py"
""",
    )
    cfg = load_config(yml)
    plan = cfg.cycles["my-cycle"]
    assert plan.trigger is not None
    assert isinstance(plan.trigger, CycleTrigger)
    assert plan.trigger.paths == ("services/db/**", "shared/**/*.py")
    assert plan.trigger.since_ref is None


def test_trigger_since_ref(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(
        yml,
        cycle_extra="""
    trigger:
      paths:
        - "src/**"
      since_ref: origin/main
""",
    )
    cfg = load_config(yml)
    assert cfg.cycles["my-cycle"].trigger is not None
    assert cfg.cycles["my-cycle"].trigger.since_ref == "origin/main"


def test_trigger_invalid_not_mapping(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(yml, cycle_extra="    trigger: not-a-mapping\n")
    with pytest.raises(ConfigValidationError, match="trigger"):
        load_config(yml)


def test_trigger_paths_empty(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(
        yml,
        cycle_extra="""
    trigger:
      paths: []
""",
    )
    with pytest.raises(ConfigValidationError, match="trigger.paths"):
        load_config(yml)


def test_cycle_name_all_reserved(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(yml, cycle_name="all")
    with pytest.raises(ConfigValidationError, match="reserved"):
        load_config(yml)


def test_cycle_name_rejects_parent_directory_segments(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_minimal_cycle_yaml(yml, cycle_name='"../outside"')
    with pytest.raises(ConfigValidationError, match="cycle name.*must not contain"):
        load_config(yml)


def test_stage_name_rejects_parent_directory_segments(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    yml.write_text(
        """
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
cycles:
  c1:
    stages:
      - name: "../outside-stage"
        equipment: pytest
        args: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigValidationError, match="stage .* must not contain"):
        load_config(yml)
