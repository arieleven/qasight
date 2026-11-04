"""Path helpers shared by the reporting submodules.

``safe_child_path`` and ``plan_artifacts_dir`` live in :mod:`qasight.paths` (the layout
every layer shares) and are re-exported here for the reporting code and older imports.
"""

from __future__ import annotations

import os
from pathlib import Path

from qasight.paths import plan_artifacts_dir, safe_child_path

__all__ = [
    "discover_latest_plan_dir",
    "discover_plan_dirs",
    "plan_artifacts_dir",
    "relpath_for_display",
    "safe_child_path",
]


def discover_plan_dirs(artifacts_root: Path) -> list[Path]:
    """Return every ``<artifacts>/<plan>/`` directory that looks like a run."""
    root = artifacts_root.expanduser().resolve()
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if (child / "events.ndjson").is_file() or any(child.glob("*/allure-results")):
            out.append(child)
    return sorted(out)


def discover_latest_plan_dir(artifacts_root: Path) -> Path | None:
    """Return the most recently updated plan directory under ``artifacts_root``.

    Prefers ``mtime`` of ``events.ndjson`` when present; otherwise uses the plan
    directory's ``mtime``. Returns ``None`` when no candidate plan dirs exist.
    """
    candidates = discover_plan_dirs(artifacts_root)
    if not candidates:
        return None

    def score(plan_dir: Path) -> float:
        ev = plan_dir / "events.ndjson"
        try:
            if ev.is_file():
                return float(ev.stat().st_mtime)
        except OSError:
            pass
        try:
            return float(plan_dir.stat().st_mtime)
        except OSError:
            return 0.0

    return max(candidates, key=score)


def relpath_for_display(path: Path, *, cwd: Path | None = None) -> str:
    """Return a cwd-relative path string suitable for terminal copy (e.g. ``./artifacts/report/index.html``)."""
    cwd = (cwd or Path.cwd()).expanduser().resolve()
    p = path.expanduser().resolve()
    try:
        rel = p.relative_to(cwd)
    except ValueError:
        try:
            rel = Path(os.path.relpath(str(p), start=str(cwd)))
        except ValueError:
            return str(p)
    text = rel.as_posix()
    if text == ".":
        return "."
    if text.startswith("../"):
        return text
    return f"./{text}"
