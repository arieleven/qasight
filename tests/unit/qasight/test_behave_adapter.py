"""Tests for :class:`~qasight.frameworks.behave_adapter.BehaveAdapter`."""

from __future__ import annotations

from pathlib import Path

from qasight.frameworks.behave_adapter import BehaveAdapter


def test_behave_adapter_has_no_native_report(tmp_path: Path) -> None:
    assert BehaveAdapter().native_report(tmp_path) is None


def _argv(tmp_path: Path, *stage_args: str) -> list[str]:
    return BehaveAdapter().build_argv(
        target_repo=tmp_path / "repo",
        results_dir=tmp_path / "results",
        stage_args=stage_args,
        workers=4,
    )


def test_build_argv_wires_the_allure_formatter_and_creates_results_dir(tmp_path: Path) -> None:
    argv = _argv(tmp_path, "features/smoke.feature")

    assert argv[:5] == [
        "behave",
        "-f",
        "allure_behave.formatter:AllureFormatter",
        "-o",
        str((tmp_path / "results").resolve()),
    ]
    assert (tmp_path / "results").is_dir()


def test_explicit_feature_target_is_kept_as_is(tmp_path: Path) -> None:
    argv = _argv(tmp_path, "--tags", "@smoke", "features/smoke.feature")

    assert argv[5:] == ["--tags", "@smoke", "features/smoke.feature"]


def test_flags_only_default_to_the_repo_features_dir(tmp_path: Path) -> None:
    argv = _argv(tmp_path, "--no-capture", "-q")

    assert argv[5:] == ["--no-capture", "-q", str((tmp_path / "repo" / "features").resolve())]


def test_workers_are_ignored(tmp_path: Path) -> None:
    """Native Behave is single-process; parallelism is BehaveX's job."""
    assert "--parallel-processes" not in _argv(tmp_path, "features/a.feature")
