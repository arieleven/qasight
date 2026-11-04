"""Plan/stage resolution: env interpolation, plan selection, ``if`` evaluation.

The loader produces an immutable :class:`QASightConfig`.  The resolver
applies the runtime concerns (env substitution, conditional stage inclusion)
just before the orchestrator executes the plan.  Keeping the two phases
separate makes ``qasight plans show`` deterministic regardless of the host
environment.
"""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Mapping

from qasight.config import if_expr
from qasight.config.errors import UNSPECIFIED_PLAN, PlanNotFoundError
from qasight.config.schema import Plan, QASightConfig, Stage

# With TESTO_RUNNER=container a stage runs in a throwaway container that must never
# see the server's own credentials, so ``${env:...}`` can't copy them in either.
_SERVER_SECRET_NAME = re.compile(
    r"^(TESTO_.*|DATABASE_URL|POSTGRES_.*|.*(_API_KEY|_TOKEN|_SECRET|_PASSWORD|_DB_URL))$",
    re.IGNORECASE,
)


def interpolation_env() -> Mapping[str, str]:
    """The variables ``${env:...}`` reads: this process's, minus the server's
    secrets when stages run in containers (``TESTO_RUNNER=container``)."""
    # Read directly (not engine.backends.runner_kind): config sits below the engine.
    if (os.environ.get("TESTO_RUNNER") or "host").strip().lower() != "container":
        return os.environ
    return {k: v for k, v in os.environ.items() if not _SERVER_SECRET_NAME.match(k)}


_ENV_PATTERN = if_expr.ENV_PATTERN


def resolve_plan(config: QASightConfig, *, plan_name: str | None) -> Plan:
    """Pick the right cycle from the config.

    When ``plan_name`` is ``None`` and exactly one plan is defined, that one
    is returned.  Otherwise an explicit name is required.
    """
    if plan_name is None:
        if len(config.cycles) == 1:
            return next(iter(config.cycles.values()))
        raise PlanNotFoundError(UNSPECIFIED_PLAN, tuple(config.cycles.keys()))
    try:
        return config.cycles[plan_name]
    except KeyError as exc:
        raise PlanNotFoundError(plan_name, tuple(config.cycles.keys())) from exc


def resolve_stages_for_plan(
    plan: Plan,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[Stage, ...]:
    """Return only the stages that survive ``${env:...}`` interpolation and ``if`` filtering."""
    env_map = env if env is not None else interpolation_env()
    out: list[Stage] = []
    for stage in plan.stages:
        if not _eval_if(stage.if_expr, env=env_map):
            continue
        resolved_args = tuple(_interpolate(arg, env=env_map) for arg in stage.args)
        resolved_extra_env = tuple((k, _interpolate(v, env=env_map)) for k, v in stage.extra_env)
        # ``replace`` keeps every other Stage field (tier, junit_xml, ...) intact.
        out.append(
            dataclasses.replace(
                stage,
                args=resolved_args,
                extra_env=resolved_extra_env,
                if_expr=None,  # already evaluated
            )
        )
    return tuple(out)


def _interpolate(value: str, *, env: Mapping[str, str]) -> str:
    """Replace every ``${env:NAME[:-DEFAULT]}`` token with its value."""
    return if_expr.interpolate(value, env=env)


def _eval_if(expr: str | None, *, env: Mapping[str, str]) -> bool:
    """Evaluate a stage's ``if:`` (grammar in :mod:`qasight.config.if_expr`)."""
    return if_expr.evaluate(expr, env=env)
