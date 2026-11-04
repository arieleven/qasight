"""Aggregate a completed run's per-stage test counts into a `PyramidModel`."""

from __future__ import annotations

from qasight.config.schema import Stage
from qasight.history.views import CompletedRunView
from qasight.reporting.pyramid_viz import PyramidModel


def run_needs_config_tiers(run: CompletedRunView) -> bool:
    """True when some stage row predates per-run tiers and has no ``tier`` recorded."""

    return any(not row.get("tier") for row in run.stage_health)


def build_pyramid_model(run: CompletedRunView, stages: tuple[Stage, ...] = ()) -> PyramidModel:
    """Sum each stage's ``total_tests`` into the tier it ran with.

    The tier recorded in the run itself wins. ``stages`` (the current config) is
    only a fallback for older run records that don't carry a tier. Stages found
    in neither default to "unit", matching `Stage.tier`'s own default.
    """

    tier_by_stage_name = {stage.name: stage.tier for stage in stages}
    counts = {"unit": 0, "integration": 0, "e2e": 0}
    for stage_row in run.stage_health:
        name = stage_row.get("name")
        total = stage_row.get("total_tests")
        if name is None or not isinstance(total, int):
            continue
        tier = stage_row.get("tier") or tier_by_stage_name.get(str(name), "unit")
        counts[tier] = counts.get(tier, 0) + total
    return PyramidModel(unit=counts["unit"], integration=counts["integration"], e2e=counts["e2e"])
