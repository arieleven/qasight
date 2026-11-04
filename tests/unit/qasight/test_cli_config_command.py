"""``qasight config validate`` and ``qasight config init``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.cli.commands import config as config_cmd
from qasight.config.json_schema import SCHEMA_ID

_VALID = """\
version: 1
cycles:
  smoke:
    stages:
      - name: unit
        equipment: pytest
        target_repo: .
      - name: e2e
        equipment: playwright
        target_repo: .
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "qasight.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_validate_ok_without_executable_check(runner: CliRunner, tmp_path: Path) -> None:
    cfg = _write(tmp_path, _VALID)
    result = runner.invoke(app, ["config", "validate", "-c", str(cfg), "--no-check-executables"])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output and "cycles=1" in result.output


def test_validate_reports_missing_executables(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write(tmp_path, _VALID)
    monkeypatch.setattr(config_cmd.shutil, "which", lambda exe: None if exe == "npx" else exe)
    result = runner.invoke(app, ["config", "validate", "-c", str(cfg)])
    assert result.exit_code == 2
    assert "missing executables" in result.output
    assert "- npx" in result.output
    assert "- pytest" not in result.output


def test_validate_bad_config_exits_2(runner: CliRunner, tmp_path: Path) -> None:
    cfg = _write(tmp_path, "cycles: [not, a, mapping\n")
    result = runner.invoke(app, ["config", "validate", "-c", str(cfg)])
    assert result.exit_code == 2
    assert "config error" in result.output


def test_init_writes_a_starter_that_validates(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "qasight.yaml"
    result = runner.invoke(app, ["config", "init", "-p", str(out)])
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert text.startswith(f"# yaml-language-server: $schema={SCHEMA_ID}")
    assert set(yaml.safe_load(text)["cycles"]) == {"smoke-test"}  # nightly-build is commented out
    validated = runner.invoke(app, ["config", "validate", "-c", str(out), "--no-check-executables"])
    assert validated.exit_code == 0, validated.output


def test_init_refuses_to_overwrite_without_force(runner: CliRunner, tmp_path: Path) -> None:
    out = _write(tmp_path, "keep me\n")
    refused = runner.invoke(app, ["config", "init", "-p", str(out)])
    assert refused.exit_code == 2
    assert out.read_text(encoding="utf-8") == "keep me\n"
    forced = runner.invoke(app, ["config", "init", "-p", str(out), "--force"])
    assert forced.exit_code == 0
    assert "cycles:" in out.read_text(encoding="utf-8")
