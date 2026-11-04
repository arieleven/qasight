"""Extra coverage for Allure input dir resolution with ``behave_native``."""

from __future__ import annotations

from pathlib import Path

from qasight.paths import allure_cli_input_directories


def test_allure_cli_input_directories_includes_behave_native(tmp_path: Path) -> None:
    root = tmp_path / "allure-results"
    root.mkdir()
    (root / "behave_native").mkdir()
    dirs = allure_cli_input_directories(root)
    assert (root / "behave_native") in dirs
