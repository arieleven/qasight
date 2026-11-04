"""Per-framework command builders consumed by :mod:`qasight.engine.executor`.

Each adapter implements the :class:`FrameworkAdapter` protocol with a tiny,
side-effect-free :meth:`build_argv` plus a :meth:`results_subdir` hint that
the orchestrator uses to lay out per-stage Allure result trees.
"""

from qasight.frameworks.base import FrameworkAdapter, get_adapter
from qasight.frameworks.behave_adapter import BehaveAdapter
from qasight.frameworks.behavex_adapter import BehaveXAdapter
from qasight.frameworks.command_adapter import CommandAdapter
from qasight.frameworks.pytest_adapter import PytestAdapter

__all__ = [
    "BehaveAdapter",
    "BehaveXAdapter",
    "CommandAdapter",
    "FrameworkAdapter",
    "PytestAdapter",
    "get_adapter",
]
