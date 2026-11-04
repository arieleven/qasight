"""``resolve_stages_for_plan`` must keep every Stage field it doesn't resolve."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from qasight.config.resolver import resolve_stages_for_plan
from qasight.config.schema import Plan, Stage
from qasight.resources.pools import ResourceItem, ResourcePool, StageResources
from qasight.services.cycle_run import apply_workers_override

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _stage() -> Stage:
    """A stage where every field differs from its default."""
    return Stage(
        name="jest",
        framework="command",
        target_repo=Path("/repo"),
        args=("npx", "jest", "--shard=${env:SHARD:-1}"),
        workers=2,
        timeout_s=30.0,
        if_expr='${env:CI} == "true"',
        extra_env=(("NODE_ENV", "${env:MODE:-test}"),),
        tier="e2e",
        junit_xml=("reports/*.xml",),
        resources=StageResources(
            pool=ResourcePool(name="vms", items=(ResourceItem(id="vm-1"),)), per="test"
        ),
        workers_auto=True,
        workers_explicit=False,
    )


def _plan(stage: Stage) -> Plan:
    return Plan(name="app", description=None, stages=(stage,))


def test_resolver_keeps_tier_and_junit_xml() -> None:
    (resolved,) = resolve_stages_for_plan(_plan(_stage()), env={"CI": "true"})

    assert resolved.tier == "e2e"
    assert resolved.junit_xml == ("reports/*.xml",)


def test_resolver_changes_only_the_fields_it_resolves() -> None:
    stage = _stage()
    (resolved,) = resolve_stages_for_plan(_plan(stage), env={"CI": "true", "SHARD": "3"})

    assert resolved == dataclasses.replace(
        stage,
        args=("npx", "jest", "--shard=3"),
        extra_env=(("NODE_ENV", "test"),),
        if_expr=None,
    )


def test_workers_override_changes_only_workers() -> None:
    stage = _stage()

    (overridden,) = apply_workers_override(_plan(stage), [stage], 7).stages

    assert overridden == dataclasses.replace(
        stage, workers=7, workers_auto=False, workers_explicit=True
    )


def test_fixture_covers_every_stage_field() -> None:
    """Guard for the tests above: a new Stage field must be given a non-default value here."""
    stage = _stage()
    defaults = Stage(name="", framework="", target_repo=Path())
    left_default = [
        f.name
        for f in dataclasses.fields(Stage)
        if f.name not in {"name", "framework", "target_repo"}
        and getattr(stage, f.name) == getattr(defaults, f.name)
    ]
    assert left_default == []
