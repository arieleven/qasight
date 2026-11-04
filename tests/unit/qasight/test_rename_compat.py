"""The names from before the rename to QASight (1.6) keep working."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import qasight
from qasight.config.database_section import discover_config_path, load_raw_config_dict
from qasight.config.loader import discover_and_load

_YAML = """\
version: 1
cycles:
  smoke:
    stages:
      - name: unit
        equipment: pytest
"""


def test_qasight_settings_fill_their_testo_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QASIGHT_RENAME_PROBE", "new")
    monkeypatch.setenv("QASIGHT_RENAME_BOTH", "new")
    monkeypatch.setenv("TESTO_RENAME_BOTH", "old")
    monkeypatch.delenv("TESTO_RENAME_PROBE", raising=False)
    qasight._mirror_env_prefix()
    assert os.environ["TESTO_RENAME_PROBE"] == "new"
    # An explicit TESTO_ setting wins.
    assert os.environ["TESTO_RENAME_BOTH"] == "old"
    monkeypatch.delenv("TESTO_RENAME_PROBE")


def test_testosterone_yaml_is_still_found(tmp_path: Path) -> None:
    (tmp_path / "testosterone.yaml").write_text(_YAML, encoding="utf-8")
    cfg = discover_and_load(cwd=tmp_path)
    assert "smoke" in cfg.cycles
    assert discover_config_path(cwd=tmp_path) == tmp_path / "testosterone.yaml"


def test_qasight_yaml_wins_over_the_old_name(tmp_path: Path) -> None:
    (tmp_path / "testosterone.yaml").write_text(_YAML.replace("smoke", "old"), encoding="utf-8")
    (tmp_path / "qasight.yaml").write_text(_YAML, encoding="utf-8")
    assert "smoke" in discover_and_load(cwd=tmp_path).cycles


def test_tool_testosterone_table_is_still_read(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.testosterone]\nversion = 1\n\n[tool.testosterone.cycles.smoke]\n"
        'stages = [{ name = "unit", equipment = "pytest" }]\n',
        encoding="utf-8",
    )
    assert "smoke" in discover_and_load(cwd=tmp_path).cycles
    assert load_raw_config_dict(tmp_path / "pyproject.toml")["version"] == 1
