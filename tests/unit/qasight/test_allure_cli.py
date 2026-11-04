"""Tests for Allure Report 3 CLI resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from qasight.reporting import allure_cli as cli


def test_resolve_prefers_local_node_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    bin_dir = root / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    allure_bin = bin_dir / "allure"
    allure_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "allurerc.mjs").write_text("export default {}", encoding="utf-8")

    monkeypatch.delenv("TESTO_ALLURE_BIN", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    cmd = cli.resolve_allure_command(repo_root=root)
    assert cmd.argv == (str(allure_bin),)
    assert cmd.cwd == root.resolve()


def test_resolve_testo_allure_bin_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_ALLURE_BIN", "/custom/allure")
    cmd = cli.resolve_allure_command(repo_root=tmp_path)
    assert cmd.argv == ("/custom/allure",)


def test_build_generate_argv_uses_awesome_for_single_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "allurerc.mjs").write_text("export default {}", encoding="utf-8")
    fake_allure = str(root / "node_modules" / ".bin" / "allure")
    fake_cmd = cli.AllureCommand(argv=(fake_allure,), cwd=root)
    monkeypatch.setattr(cli, "resolve_allure_command", lambda **_kw: fake_cmd)

    results = root / "allure-results"
    results.mkdir()
    out = root / "out"
    argv = cli.build_generate_argv(result_dirs=[results], out_dir=out, single_file=True)
    assert "awesome" in argv
    assert "--single-file" in argv
    assert "--output" in argv


def test_is_allure_available_false_when_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli, "resolve_allure_command", MagicMock(side_effect=cli.AllureCLINotFoundError("x"))
    )
    assert cli.is_allure_available() is False
