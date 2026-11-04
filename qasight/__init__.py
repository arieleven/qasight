"""QASight — public API for the ``qasight`` distribution.

The new narrow surface is :class:`qasight.config.Plan` /
:class:`qasight.config.Stage` plus :func:`qasight.engine.run_plan`.
Every name is exposed via PEP 562 lazy loading, so ``import qasight`` (which
pytest does for every run through the ``pytest11`` plugin) loads no config,
engine or SQLAlchemy code until a consumer actually touches a name. Loading
them eagerly also hid those modules from pytest-cov, which starts after plugins.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import TYPE_CHECKING, Any


def _mirror_env_prefix() -> None:
    """Let ``QASIGHT_*`` stand in for every ``TESTO_*`` setting.

    Settings kept their ``TESTO_`` names through the rename to QASight (1.6), so
    nothing that already sets them breaks. ``QASIGHT_X`` fills ``TESTO_X`` when that
    isn't set; an explicit ``TESTO_X`` wins. Child processes inherit the result.
    """
    for key, value in list(os.environ.items()):
        if key.startswith("QASIGHT_") and len(key) > len("QASIGHT_"):
            os.environ.setdefault("TESTO_" + key[len("QASIGHT_") :], value)


_mirror_env_prefix()

if TYPE_CHECKING:
    from qasight.config import Plan as Plan
    from qasight.config import QASightConfig as QASightConfig
    from qasight.config import Stage as Stage
    from qasight.config import TestosteroneConfig as TestosteroneConfig
    from qasight.engine.exit_codes import EngineExitCode as EngineExitCode

# Each entry maps the exported attribute to its real module path. PEP 562
# :func:`__getattr__` resolves them on first access, so ``import qasight``
# loads nothing else.
_LAZY_EXPORTS: dict[str, str] = {
    "Plan": "qasight.config",
    "Stage": "qasight.config",
    "QASightConfig": "qasight.config",
    "TestosteroneConfig": "qasight.config",
    "EngineExitCode": "qasight.engine.exit_codes",
    "get_repository": "qasight.repository.db",
    "reset_repository_cache": "qasight.repository.db",
    "create_db_and_tables": "qasight.repository.db_config",
    "get_engine": "qasight.repository.db_config",
    "reset_engine_cache": "qasight.repository.db_config",
    "resolve_database_url": "qasight.repository.db_config",
    "RunRecord": "qasight.repository.models",
    "RunStatus": "qasight.repository.models",
}


__all__ = [*_LAZY_EXPORTS.keys()]


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute resolver for the names above."""
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module 'qasight' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value  # cache for subsequent accesses
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


try:
    __version__ = pkg_version("qasight")
except PackageNotFoundError:  # pragma: no cover - source tree import before install
    __version__ = "0.1.0"
