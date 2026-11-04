"""``testo_api`` is now ``qasight_api``: see ``testo_core`` for how the alias works."""

from __future__ import annotations

import importlib
import sys
import warnings

import testo_core  # noqa: F401  (installs the alias for testo_core.* and testo_api.*)

warnings.warn(
    "'testo_api' is now 'qasight_api' (testo-core was renamed to qasight in 1.6); "
    "import qasight_api instead.",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules["testo_api"] = importlib.import_module("qasight_api")
