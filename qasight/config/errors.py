"""Exception taxonomy for the configuration layer.

``qasight.config`` defines its own errors so it can be imported without
pulling in the engine. The CLI maps them to exit code 2 (invalid input).
"""

from __future__ import annotations

#: Plan name used when no ``--cycle`` was given and the config has several.
UNSPECIFIED_PLAN = "<unspecified>"


class ConfigError(Exception):
    """Base class for any configuration-layer failure."""


class ConfigValidationError(ConfigError):
    """The YAML/TOML config is missing required keys or has invalid types."""


class PlanNotFoundError(ConfigError):
    """User asked for a plan that does not exist in the config."""

    def __init__(self, plan_name: str, available: tuple[str, ...]) -> None:
        avail = ", ".join(available) if available else "(none)"
        if plan_name == UNSPECIFIED_PLAN:
            message = (
                f"the config defines several cycles; pick one with --cycle. Available: {avail}."
            )
        else:
            message = f"cycle {plan_name!r} not found. Available: {avail}."
        super().__init__(message)
        self.plan_name = plan_name
        self.available = available


class ConfigDiscoveryError(ConfigError):
    """No config file was found in the search path."""
