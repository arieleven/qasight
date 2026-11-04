"""Tests for ``qasight config-db``."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_config_db_writes_yaml(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(
        "version: 1\ndefaults: {target_repo: ., artifacts_root: artifacts}\n"
        "cycles:\n  c:\n    stages:\n      - {name: s, equipment: pytest, args: ['-q']}\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "config-db",
            "--config",
            str(cfg),
            "--url",
            "sqlite:///cli_config.db",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    text = cfg.read_text(encoding="utf-8")
    assert "cli_config.db" in text


def test_config_db_connection_probe_failure(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreachable DB must fail before rewriting config."""
    from unittest.mock import MagicMock, patch

    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(
        "version: 1\ndefaults: {target_repo: ., artifacts_root: artifacts}\n"
        "cycles:\n  c:\n    stages:\n      - {name: s, equipment: pytest, args: ['-q']}\n",
        encoding="utf-8",
    )
    before = cfg.read_text(encoding="utf-8")
    engine = MagicMock()

    def _connect_fail(*_a: object, **_k: object) -> None:
        raise OSError("connection refused")

    engine.connect.side_effect = _connect_fail
    with patch("sqlalchemy.create_engine", return_value=engine):
        result = runner.invoke(
            app,
            [
                "config-db",
                "--config",
                str(cfg),
                "--url",
                "sqlite:///probe_should_not_be_written.db",
            ],
        )
    assert result.exit_code != 0
    combined = (result.stdout + result.stderr).lower()
    assert "connection" in combined or "refused" in combined
    assert cfg.read_text(encoding="utf-8") == before


def test_config_db_rejects_bad_url(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(
        "version: 1\ndefaults: {target_repo: ., artifacts_root: artifacts}\n"
        "cycles:\n  c:\n    stages:\n      - {name: s, equipment: pytest, args: ['-q']}\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["config-db", "--config", str(cfg), "--url", "oracle://nope"],
    )
    assert result.exit_code != 0
