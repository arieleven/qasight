"""A run only reports the stages it ran (P2 in docs/engine-cli-review.md).

A stage removed, renamed or skipped by ``if:`` / ``fail_fast`` used to keep its old
folder under ``artifacts/<cycle>/``, and every reader (reports, archive metrics,
history snapshot) counted it in later runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.schema import Plan, Stage
from qasight.engine.orchestrator import run_plan
from qasight.reporting.collector import collect_results
from tests.fixtures.engine import NoopRenderer, use_echo_adapter

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _plan(*names: str) -> Plan:
    stages = tuple(
        Stage(name=n, framework="pytest", target_repo=Path("."), timeout_s=30.0) for n in names
    )
    return Plan(name="web", description=None, stages=stages)


def _run(plan: Plan, root: Path, *, fail_fast: bool = False) -> None:
    run_plan(plan, renderer=NoopRenderer(), artifacts_root=root, persist=False, fail_fast=fail_fast)


def test_a_removed_stage_is_not_collected_by_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    _run(_plan("unit", "e2e"), tmp_path)
    assert {s.stage for s in collect_results(tmp_path, plan_name="web").stages} >= {"e2e"}

    _run(_plan("unit"), tmp_path)

    assert not (tmp_path / "web" / "e2e").exists()
    assert {s.stage for s in collect_results(tmp_path, plan_name="web").stages} <= {"unit"}
    assert (tmp_path / "web" / "unit").is_dir()


def test_other_folders_under_the_cycle_stay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    keep = tmp_path / "web" / "notes"
    keep.mkdir(parents=True)
    (keep / "readme.txt").write_text("mine", encoding="utf-8")

    _run(_plan("unit"), tmp_path)

    assert (keep / "readme.txt").read_text(encoding="utf-8") == "mine"
