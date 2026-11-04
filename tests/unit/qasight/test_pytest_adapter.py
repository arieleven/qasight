"""Tests for :class:`~qasight.frameworks.pytest_adapter.PytestAdapter`."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from qasight.frameworks import pytest_adapter
from qasight.frameworks.pytest_adapter import PytestAdapter


@pytest.fixture(autouse=True)
def _no_xdist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep argv deterministic: tests that want xdist opt in explicitly."""
    monkeypatch.setattr(pytest_adapter, "xdist_available", lambda: False)


def _argv(tmp_path: Path, *, args: tuple[str, ...] = ("-q",), workers: int) -> list[str]:
    return PytestAdapter().build_argv(
        target_repo=tmp_path, results_dir=tmp_path / "out", stage_args=args, workers=workers
    )


def test_workers_become_xdist_n_when_xdist_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pytest_adapter, "xdist_available", lambda: True)
    argv = _argv(tmp_path, workers=4)
    assert argv[-3:-1] == ["-n", "4"]
    assert argv[-1].startswith("--alluredir=")


def test_workers_without_xdist_run_serially_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=pytest_adapter.__name__):
        argv = _argv(tmp_path, workers=4)
    assert "-n" not in argv
    assert "pytest-xdist is not installed" in caplog.text


def test_single_worker_never_checks_for_xdist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail() -> bool:
        raise AssertionError("xdist probe should not run for workers=1")

    monkeypatch.setattr(pytest_adapter, "xdist_available", _fail)
    assert "-n" not in _argv(tmp_path, workers=1)


@pytest.mark.parametrize(
    "args", [("-n", "2"), ("-nauto",), ("--numprocesses=3",), ("-p", "no:xdist")]
)
def test_stage_args_choosing_xdist_win_over_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    monkeypatch.setattr(pytest_adapter, "xdist_available", lambda: True)
    argv = _argv(tmp_path, args=args, workers=4)
    assert argv[1:-1] == list(args)


def test_xdist_probe_uses_the_interpreter_behind_pytest_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stage runs whatever ``pytest`` is on PATH, which may live in another
    env than qasight; the probe must ask that env's Python."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)
    fake_pytest = bin_dir / "pytest"
    fake_pytest.write_text(f"#!{fake_python}\n", encoding="utf-8")
    fake_pytest.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))

    assert pytest_adapter._pytest_interpreter() == str(fake_python)
    monkeypatch.undo()  # drop the autouse stub (and PATH) before the real probe
    monkeypatch.setenv("PATH", str(bin_dir))
    assert pytest_adapter.xdist_available() is True  # fake python exits 0

    fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    assert pytest_adapter.xdist_available() is False


def test_pytest_adapter_injects_config_when_target_has_pytest_ini(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    ad = PytestAdapter()
    argv = ad.build_argv(
        target_repo=repo,
        results_dir=tmp_path / "out",
        stage_args=("-q", "tests"),
        workers=4,
    )
    assert argv[0] == "pytest"
    assert argv[1] == "-c"
    assert argv[2] == str((repo / "pytest.ini").resolve())


def test_pytest_adapter_skips_inject_when_user_passes_c(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    ad = PytestAdapter()
    argv = ad.build_argv(
        target_repo=repo,
        results_dir=tmp_path / "out",
        stage_args=("-c", "/other/pytest.ini", "-q"),
        workers=4,
    )
    assert argv[1:3] == ["-c", "/other/pytest.ini"]


def test_pytest_adapter_has_no_native_report(tmp_path: Path) -> None:
    assert PytestAdapter().native_report(tmp_path) is None


def test_pytest_adapter_passes_alluredir_as_one_token(tmp_path: Path) -> None:
    argv = PytestAdapter().build_argv(
        target_repo=tmp_path,
        results_dir=tmp_path / "out",
        stage_args=("-q",),
        workers=1,
    )
    assert argv[-1] == f"--alluredir={(tmp_path / 'out').resolve()}"


def test_target_inside_another_pytest_project_keeps_its_own_rootdir(tmp_path: Path) -> None:
    """A target nested in a project with its own ``pytest.ini`` (qasight's
    checkout, in CI) must still use its own config. Before the fix, the results
    path pulled pytest's rootdir up to the outer project and the target's
    ``pythonpath`` was ignored, so every import of the target's package failed.
    """
    import subprocess
    import sys

    outer = tmp_path / "outer"
    target = outer / ".demo" / "app"
    (target / "pkg").mkdir(parents=True)
    (target / "tests").mkdir()
    (outer / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (target / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["."]\n', encoding="utf-8"
    )
    (target / "pkg" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (target / "tests" / "test_pkg.py").write_text(
        "from pkg import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n", encoding="utf-8"
    )

    # The engine creates the results directory before the stage runs; only an
    # existing path can move pytest's rootdir.
    results = outer / "artifacts" / "stage" / "allure-results"
    results.mkdir(parents=True)

    argv = PytestAdapter().build_argv(
        target_repo=target,
        results_dir=results,
        stage_args=("-q", "-p", "no:cacheprovider", "tests"),
        workers=1,
    )
    # ``-P`` keeps the cwd off sys.path, as the engine's console script does;
    # plain ``python -m pytest`` would put it there and hide a wrong rootdir.
    # Using this interpreter, not a ``pytest`` found on PATH, keeps the plugins
    # (allure-pytest for ``--alluredir``) the ones this suite runs with.
    proc = subprocess.run(
        [sys.executable, "-P", "-m", "pytest", *argv[1:]],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_warns_when_the_pytest_on_path_has_no_allure_plugin(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # pipx / uv tool installs: qasight's env has allure-pytest, the pytest on PATH doesn't.
    monkeypatch.setattr(
        pytest_adapter, "_pytest_can_import", lambda module: module != "allure_pytest"
    )
    with caplog.at_level("WARNING", logger=pytest_adapter.__name__):
        pytest_adapter.warn_if_allure_missing()
    assert "allure-pytest" in caplog.text and "--alluredir" in caplog.text


def test_no_warning_when_allure_plugin_loads(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(pytest_adapter, "_pytest_can_import", lambda module: True)
    with caplog.at_level("WARNING", logger=pytest_adapter.__name__):
        pytest_adapter.warn_if_allure_missing()
    assert caplog.text == ""


def test_default_workers_go_serial_quietly_without_xdist() -> None:
    assert pytest_adapter.default_workers(4) == 1


def test_default_workers_keep_their_value_with_xdist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pytest_adapter, "xdist_available", lambda: True)
    assert pytest_adapter.default_workers(4) == 4
