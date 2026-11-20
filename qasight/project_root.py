"""Find the project folder from anywhere inside it, the way git finds ``.git``.

``qasight flaky`` run from ``tests/`` used to look for ``qasight.yaml``, ``.testo/``
and ``testo_history.db`` in ``tests/`` only, find nothing and report an empty
history. Coding agents often work from a subfolder, so they drew the wrong
conclusion. Now every lookup starts at the nearest folder, at or above the
current one, that holds QASight's own files.

The walk stops at the repository root (the folder with ``.git``) and never
looks at the home folder or above it. When nothing is found, the current folder
is used, as before, so a fresh project still gets its files where it was started.
``TESTO_HOME`` and ``DATABASE_URL`` still win over all of this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from qasight.paths import CONFIG_FILE_NAMES, DEFAULT_DATA_DIRNAME

HISTORY_DB_NAME: Final[str] = "testo_history.db"

# A folder holding any of these is a QASight project folder.
_MARKERS: Final[tuple[str, ...]] = (*CONFIG_FILE_NAMES, DEFAULT_DATA_DIRNAME, HISTORY_DB_NAME)


def _has_pyproject_table(folder: Path) -> bool:
    path = folder / "pyproject.toml"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "[tool.qasight]" in text or "[tool.testosterone]" in text


def is_project_folder(folder: Path) -> bool:
    """Whether *folder* holds a QASight config, data folder or history database."""
    return any((folder / name).exists() for name in _MARKERS) or _has_pyproject_table(folder)


def find_project_root(cwd: Path | None = None) -> Path:
    """The nearest QASight project folder at or above *cwd*, else *cwd* itself."""
    start = (cwd or Path.cwd()).expanduser().resolve()
    try:
        home = Path.home().resolve()
    except (RuntimeError, KeyError):  # no home folder (some containers)
        home = None
    for folder in (start, *start.parents):
        if home is not None and (folder == home or folder in home.parents):
            break
        if is_project_folder(folder):
            return folder
        if (folder / ".git").exists():
            break
    return start
