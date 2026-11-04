"""Plan-aware configuration layer for the ``qasight`` CLI.

The runtime engine consumes :class:`Plan` objects produced by this package;
it must not parse YAML itself.
"""

from qasight.config.errors import (
    ConfigError,
    ConfigValidationError,
    PlanNotFoundError,
)
from qasight.config.loader import discover_and_load, load_config
from qasight.config.resolver import resolve_plan, resolve_stages_for_plan
from qasight.config.schema import Defaults, Plan, QASightConfig, Stage, TestosteroneConfig

__all__ = [
    "ConfigError",
    "ConfigValidationError",
    "Defaults",
    "Plan",
    "PlanNotFoundError",
    "Stage",
    "QASightConfig",
    "TestosteroneConfig",
    "discover_and_load",
    "load_config",
    "resolve_plan",
    "resolve_stages_for_plan",
]
