"""Shared fixtures for the ``qasight run`` CLI suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolated_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the history DB at a throwaway SQLite file.

    Without this, ``run_plan(persist=True)`` falls back to
    ``sqlite:///./testo_history.db`` in the repo checkout.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'testo_history.db'}")


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()
