"""Filesystem + Docker cleanup helpers for ``qasight clean``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from qasight.paths import CONFIG_FILE_NAMES

PROJECT_DOCKER_LABEL = "com.testosterone.project=testo"


_PROJECT_MARKERS = (".git", *CONFIG_FILE_NAMES, "pyproject.toml")


def unsafe_to_remove(path: Path, *, protected: tuple[Path, ...] = ()) -> str | None:
    """Why ``qasight clean`` must not delete *path*, or ``None`` when it may.

    Refuses the filesystem root, the home folder, the current folder, any
    *protected* folder (the config's folder), a parent of any of those, and a
    folder that looks like a project (it holds ``.git``, ``qasight.yaml``
    or ``pyproject.toml``). ``artifacts_root: .`` would otherwise delete the
    whole project.
    """
    p = path.expanduser().resolve()
    keep = {Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve()}
    keep |= {q.expanduser().resolve() for q in protected}
    for kept in keep:
        if p == kept or p in kept.parents:
            return f"{p} is (or contains) {kept}"
    for marker in _PROJECT_MARKERS:
        if (p / marker).exists():
            return f"{p} looks like a project folder (it has {marker})"
    return None


def remove_tree_if_exists(path: Path) -> bool:
    """Delete ``path`` if it exists and is a directory. Returns whether anything was removed."""
    p = path.expanduser().resolve()
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=False)
        return True
    return False


def docker_prune_stopped_with_label(*, label: str = PROJECT_DOCKER_LABEL) -> tuple[int, str]:
    """Run ``docker container prune`` filtered by label; returns (exit_code, combined stderr+stdout)."""
    try:
        proc = subprocess.run(  # noqa: S603, S607
            [
                "docker",
                "container",
                "prune",
                "-f",
                f"--filter=label={label}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out
    except FileNotFoundError:
        return 127, "docker: command not found"
    except subprocess.TimeoutExpired:
        return 124, "docker prune timed out"
