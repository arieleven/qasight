"""Where the built web UI is: ``TESTO_UI_DIR``, else the one a released wheel carries.

The API serves it (``testo_api/ui.py``) and ``testo export`` inlines it into a
single-file report, so both find it here. A released wheel carries the build in
``testo_api/ui_dist/``; it is located without importing ``testo_api``, which
``testo_core`` must not depend on.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

UI_DIR_ENV = "TESTO_UI_DIR"


def bundled_ui_dir() -> Path | None:
    """``testo_api/ui_dist`` next to the installed ``testo_api`` package (it may be empty)."""
    spec = importlib.util.find_spec("testo_api")
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations))).resolve() / "ui_dist"


def ui_dir() -> Path | None:
    """The UI build to use: ``TESTO_UI_DIR`` if set, else the bundled one, else None."""
    raw = (os.environ.get(UI_DIR_ENV) or "").strip()
    path = Path(raw).expanduser().resolve() if raw else bundled_ui_dir()
    return path if path is not None and (path / "index.html").is_file() else None
