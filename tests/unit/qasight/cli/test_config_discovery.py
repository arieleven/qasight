"""Config discovery tests (QA Strategies EC-02 row / layout table).

Locks the discovery order documented in ``qasight/config/loader.py``:
``--config PATH`` → ``./qasight.yaml`` → ``./qasight.yml`` →
``[tool.qasight]`` in ``./pyproject.toml``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.errors import ConfigDiscoveryError
from qasight.config.loader import discover_and_load
from tests.fixtures.engine import use_echo_adapter, write_minimal_config

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _write_yml(root: Path) -> Path:
    path = root / "qasight.yml"
    path.write_text(
        textwrap.dedent(
            """
            version: 1
            cycles:
              from-yml:
                stages:
                  - name: yml-stage
                    equipment: pytest
            """
        ),
        encoding="utf-8",
    )
    return path


def _write_pyproject(root: Path) -> Path:
    path = root / "pyproject.toml"
    path.write_text(
        textwrap.dedent(
            """
            [tool.qasight]
            version = 1

            [tool.qasight.cycles.from-pyproject]
            stages = [{ name = "pyproject-stage", equipment = "pytest" }]
            """
        ),
        encoding="utf-8",
    )
    return path


def test_yaml_wins_over_yml_and_pyproject(tmp_path: Path) -> None:
    yaml_path = write_minimal_config(tmp_path)  # qasight.yaml, cycle "smoke"
    _write_yml(tmp_path)
    _write_pyproject(tmp_path)

    cfg = discover_and_load(cwd=tmp_path)

    assert cfg.source_path == yaml_path
    assert list(cfg.cycles) == ["smoke"]


def test_yml_wins_over_pyproject(tmp_path: Path) -> None:
    _write_yml(tmp_path)
    _write_pyproject(tmp_path)

    cfg = discover_and_load(cwd=tmp_path)

    assert list(cfg.cycles) == ["from-yml"]


def test_pyproject_table_is_the_last_fallback(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)

    cfg = discover_and_load(cwd=tmp_path)

    assert list(cfg.cycles) == ["from-pyproject"]


def test_no_config_anywhere_raises_discovery_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigDiscoveryError):
        discover_and_load(cwd=tmp_path)


def test_pyproject_without_table_raises_discovery_error(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

    with pytest.raises(ConfigDiscoveryError):
        discover_and_load(cwd=tmp_path)


def test_explicit_config_path_bypasses_discovery(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    explicit = write_minimal_config(nested)
    _write_yml(tmp_path)  # would win discovery if cwd were used

    cfg = discover_and_load(config_path=explicit, cwd=tmp_path)

    assert cfg.source_path == explicit


def test_cli_discovers_config_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    write_minimal_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = cli_runner.invoke(app, ["run", "--cycle", "smoke", "--no-report-db"])

    assert result.exit_code == 0
    assert (tmp_path / "artifacts" / "smoke" / "events.ndjson").is_file()


def test_cli_explicit_missing_config_exits_2(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = cli_runner.invoke(
        app, ["run", "--cycle", "smoke", "--config", str(tmp_path / "ghost.yaml"), "--no-report-db"]
    )
    assert result.exit_code == 2
