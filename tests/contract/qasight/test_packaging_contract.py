from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path

import pytest

import qasight

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    """Drop ANSI escape sequences so substring assertions survive Rich/Typer
    colorizing the output (e.g. ``\\x1b[1m--\\x1b[0mci``)."""
    return _ANSI_RE.sub("", text)


def _help_env() -> dict[str, str]:
    """Subprocess env that forces plain, wide help output.

    * ``NO_COLOR=1`` — universal "no ANSI" opt-in (Rich, Click, Typer all honor it).
    * ``TERM=dumb`` — keeps Rich/Click from auto-detecting a TTY.
    * ``COLUMNS=200`` — keeps long flag/usage lines from being wrapped on narrow
      CI runners where ``--help`` panels are otherwise re-flowed.
    """
    return {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}


@pytest.mark.contract
def test_public_version_matches_distribution_metadata() -> None:
    assert qasight.__version__ == pkg_version("qasight")


@pytest.mark.contract
def test_console_script_testo_help_works() -> None:
    testo_path = shutil.which("qasight")
    if testo_path is None:
        testo_path = str(Path(sys.prefix).resolve() / "bin" / "qasight")
    proc = subprocess.run(  # noqa: S603
        [testo_path, "run", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=_help_env(),
    )
    assert proc.returncode == 0
    stdout = _strip_ansi(proc.stdout)
    assert "Usage:" in stdout
    assert "--ci" in stdout
    assert "--cycle" in stdout


@pytest.mark.contract
def test_python_build_module_available() -> None:
    # The `dev` extra installs build; skip on a machine set up without it.
    pytest.importorskip(
        "build", reason="the `build` package is not installed (pip install -e '.[dev]')"
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "build", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
