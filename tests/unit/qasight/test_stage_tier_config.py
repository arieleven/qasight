"""Tests for ``Stage.tier`` parsing/inference in qasight.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.loader import load_config


def _write_cycle_yaml(path: Path, *, stages_yaml: str) -> None:
    body = f"""
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
cycles:
  my-cycle:
    description: d
    stages:
{stages_yaml}
"""
    path.write_text(body, encoding="utf-8")


def test_tier_defaults_by_framework(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_cycle_yaml(
        yml,
        stages_yaml="""
      - name: pytest-stage
        equipment: pytest
        args: []
      - name: behave-stage
        equipment: behave
        args: []
      - name: behavex-stage
        equipment: behavex
        args: []
""",
    )
    cfg = load_config(yml)
    stages = {s.name: s for s in cfg.cycles["my-cycle"].stages}
    assert stages["pytest-stage"].tier == "unit"
    assert stages["behave-stage"].tier == "integration"
    assert stages["behavex-stage"].tier == "e2e"


def test_tier_explicit_override(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_cycle_yaml(
        yml,
        stages_yaml="""
      - name: pytest-as-integration
        equipment: pytest
        tier: integration
        args: []
""",
    )
    cfg = load_config(yml)
    stage = cfg.cycles["my-cycle"].stages[0]
    assert stage.tier == "integration"


def test_tier_rejects_unsupported_value(tmp_path: Path) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_cycle_yaml(
        yml,
        stages_yaml="""
      - name: bad-tier
        equipment: pytest
        tier: system
        args: []
""",
    )
    with pytest.raises(ConfigValidationError, match="unsupported tier"):
        load_config(yml)


def test_workers_on_single_process_framework_warns_but_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    yml = tmp_path / "qasight.yaml"
    _write_cycle_yaml(
        yml,
        stages_yaml="""
      - name: behave-stage
        equipment: behave
        workers: 4
      - name: behavex-stage
        equipment: behavex
        workers: 4
""",
    )
    with caplog.at_level("WARNING", logger="qasight.config.sections"):
        cfg = load_config(yml)
    assert cfg.cycles["my-cycle"].stages[0].workers == 4
    assert "'behave-stage': 'workers' has no effect on framework 'behave'" in caplog.text
    assert "behavex-stage" not in caplog.text
