"""Pytest adapter — emits ``--alluredir`` pointing at the per-stage tree."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from qasight.frameworks.base import NativeReport

logger = logging.getLogger(__name__)


def _pytest_interpreter() -> str | None:
    """The Python behind the ``pytest`` on PATH (the one the stage will run).

    Read from the console script's shebang, so a pytest installed in another
    venv or tool env is checked in that env, not in qasight's. Falls back
    to this interpreter when there is no readable shebang (e.g. Windows ``.exe``).
    """
    exe = shutil.which("pytest")
    if exe is None:
        return None
    try:
        with open(exe, "rb") as fh:
            first = fh.readline(512).decode("utf-8", "replace").strip()
    except OSError:
        return sys.executable
    if not first.startswith("#!"):
        return sys.executable
    parts = first[2:].split()
    if not parts:
        return sys.executable
    if Path(parts[0]).name == "env" and len(parts) > 1:
        return shutil.which(parts[1]) or sys.executable
    if "python" not in Path(parts[0]).name:
        return sys.executable  # e.g. pip's ``#!/bin/sh`` wrapper for long paths
    return parts[0]


def _pytest_can_import(module: str) -> bool | None:
    """Whether the ``pytest`` the stage runs can import ``module``; None when unknown."""
    python = _pytest_interpreter()
    if python is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no user input
            [python, "-c", f"import {module}"], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode == 0


def xdist_available() -> bool:
    """True when the ``pytest`` the stage runs can load pytest-xdist."""
    return bool(_pytest_can_import("xdist"))


def default_workers(workers: int) -> int:
    """Workers for a stage that never set ``workers``: serial when pytest-xdist is missing.

    The built-in default is 4, so without this every first run of a project that
    doesn't use pytest-xdist warned about a setting the user never wrote.
    """
    return workers if workers <= 1 or xdist_available() else 1


def warn_if_allure_missing() -> None:
    """Explain the ``unrecognized arguments: --alluredir`` that follows otherwise.

    ``qasight`` installed with pipx or ``uv tool`` lives in its own environment, while
    the stage runs the ``pytest`` on PATH, which then has no allure-pytest plugin.
    """
    if _pytest_can_import("allure_pytest") is False:
        logger.warning(
            "pytest: the pytest on PATH (%s) can't load allure-pytest, so it will reject "
            "--alluredir. Install it next to your tests: %s -m pip install allure-pytest "
            "(or install qasight in the same environment as pytest).",
            shutil.which("pytest"),
            _pytest_interpreter(),
        )


def _args_choose_xdist(args: list[str]) -> bool:
    """True when the stage's own args already set ``-n`` or disable xdist."""
    for i, a in enumerate(args):
        if a.startswith(("-n", "--numprocesses")) or a == "-pno:xdist":
            return True
        if a == "no:xdist" and i > 0 and args[i - 1] == "-p":
            return True
    return False


def _args_set_explicit_pytest_config(args: list[str]) -> bool:
    """True when the user already chose a pytest config file (do not inject ``-c``)."""
    i = 0
    while i < len(args):
        a = str(args[i])
        if a in ("-c", "--config", "--config-file"):
            return True
        if a.startswith(("--config-file=", "--config=")):
            return True
        i += 1
    return False


class PytestAdapter:
    name: str = "pytest"

    def results_subdir(self) -> str:
        return "pytest"

    def build_argv(
        self,
        *,
        target_repo: Path,
        results_dir: Path,
        stage_args: tuple[str, ...],
        workers: int,
    ) -> list[str]:
        repo = target_repo.expanduser().resolve()
        argv: list[str] = ["pytest"]
        args = list(stage_args)
        if not _args_set_explicit_pytest_config(args):
            ini = repo / "pytest.ini"
            if ini.is_file():
                argv.extend(["-c", str(ini.resolve())])
        argv.extend(args)
        if workers > 1 and not _args_choose_xdist(args):
            if xdist_available():
                argv.extend(["-n", str(workers)])
            else:
                logger.warning(
                    "pytest: workers=%d ignored, pytest-xdist is not installed; running serially.",
                    workers,
                )
        # One token, not two: pytest picks its rootdir (and so the target's own
        # config, e.g. ``pythonpath``) from the common ancestor of every
        # non-option argument that exists on disk, and a separate path token
        # after ``--alluredir`` counts as one. With results under qasight's
        # ``artifacts/``, a target inside the checkout would get qasight's
        # rootdir and ``pytest.ini`` instead of its own.
        warn_if_allure_missing()
        argv.append(f"--alluredir={results_dir.resolve()}")
        return argv

    def native_report(self, stage_dir: Path) -> NativeReport | None:
        del stage_dir  # pytest has no native HTML report of its own
        return None
