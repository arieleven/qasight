"""``testo_core`` is now ``qasight``: old imports keep working through this alias.

``import testo_core.engine.orchestrator`` returns ``qasight.engine.orchestrator``
itself (the same module object, never a second copy), and the same for
``testo_api`` and ``qasight_api``.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import warnings
from types import ModuleType

RENAMED = {"testo_core": "qasight", "testo_api": "qasight_api"}


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target: str) -> None:
        self.target = target

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        return importlib.import_module(self.target)

    def exec_module(self, module: ModuleType) -> None:
        """The target module is already loaded."""


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        head, _, rest = fullname.partition(".")
        new = RENAMED.get(head)
        if new is None or not rest:
            return None
        return importlib.util.spec_from_loader(fullname, _AliasLoader(f"{new}.{rest}"))


def install(old_name: str) -> None:
    """Alias ``old_name`` (and everything under it) to its new package."""
    if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _AliasFinder())
    warnings.warn(
        f"'{old_name}' is now '{RENAMED[old_name]}' (testo-core was renamed to qasight in 1.6); "
        f"import {RENAMED[old_name]} instead.",
        DeprecationWarning,
        stacklevel=3,
    )
    sys.modules[old_name] = importlib.import_module(RENAMED[old_name])


install("testo_core")
