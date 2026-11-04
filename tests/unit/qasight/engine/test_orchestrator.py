"""Unit tests for ``run_plan`` fail-fast lifecycle (QA Strategies LC rows).

The executor is stubbed so each stage's returncode is scripted; assertions
cover the ``events.ndjson`` contract documented in
``docs/CLI Commands/Troubleshooting and Error Codes.md``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from qasight.config.schema import Plan, Stage
from qasight.engine import orchestrator
from qasight.engine.events import EngineEvent
from qasight.engine.exit_codes import EngineExitCode
from qasight.engine.result import StageResult
from qasight.paths import plan_artifacts_dir

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


class _RecordingRenderer:
    """Minimal event sink satisfying the orchestrator's renderer protocol."""

    wants_streaming = False

    def __init__(self) -> None:
        self.events: list[EngineEvent] = []

    def handle(self, event: EngineEvent) -> None:
        self.events.append(event)


def _stage(name: str) -> Stage:
    return Stage(name=name, framework="pytest", target_repo=Path("."))


def _plan(*stage_names: str) -> Plan:
    return Plan(
        name="fail-fast-plan", description=None, stages=tuple(_stage(n) for n in stage_names)
    )


def _stub_run_stage(monkeypatch: pytest.MonkeyPatch, returncodes: dict[str, int]) -> list[str]:
    """Replace the executor with a scripted stub; returns the call order."""
    executed: list[str] = []

    def fake_run_stage(
        stage: Stage, *, plan_name: str, artifacts_root: Path, **_: object
    ) -> StageResult:
        executed.append(stage.name)
        now = time.time()
        return StageResult(
            stage_name=stage.name,
            framework=stage.framework,
            returncode=returncodes[stage.name],
            started_at=now,
            finished_at=now,
            duration_s=0.0,
            log_path=None,
            artifacts_dir=artifacts_root / plan_name / stage.name,
            command=("pytest",),
            output_tail="",
        )

    monkeypatch.setattr(orchestrator, "run_stage", fake_run_stage)
    return executed


def _read_events(artifacts_root: Path, plan_name: str) -> list[dict[str, object]]:
    events_path = plan_artifacts_dir(artifacts_root, plan_name) / "events.ndjson"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_fail_fast_aborts_after_first_failing_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan("lint", "unit", "e2e")
    executed = _stub_run_stage(monkeypatch, {"lint": 0, "unit": 1, "e2e": 0})

    result = orchestrator.run_plan(
        plan,
        renderer=_RecordingRenderer(),
        artifacts_root=tmp_path,
        persist=False,
        fail_fast=True,
    )

    assert executed == ["lint", "unit"]
    assert len(result.stages) == 2
    assert result.aggregate_returncode == 1
    assert result.exit_code is EngineExitCode.DOMAIN_FAILURE

    events = _read_events(tmp_path, plan.name)
    kinds = [e["event"] for e in events]
    assert kinds == [
        "plan_started",
        "stage_started",
        "stage_finished",
        "stage_started",
        "stage_finished",
        "plan_aborted",
        "plan_finished",
    ]

    aborted = events[kinds.index("plan_aborted")]
    assert aborted == {
        "event": "plan_aborted",
        "plan": plan.name,
        "reason": "fail_fast",
        "completed_stages": 2,
    }


def test_fail_fast_disabled_runs_all_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan("lint", "unit", "e2e")
    executed = _stub_run_stage(monkeypatch, {"lint": 0, "unit": 1, "e2e": 0})

    result = orchestrator.run_plan(
        plan,
        renderer=_RecordingRenderer(),
        artifacts_root=tmp_path,
        persist=False,
        fail_fast=False,
    )

    assert executed == ["lint", "unit", "e2e"]
    assert len(result.stages) == 3
    assert result.exit_code is EngineExitCode.DOMAIN_FAILURE

    events = _read_events(tmp_path, plan.name)
    kinds = [e["event"] for e in events]
    assert "plan_aborted" not in kinds
    assert kinds[-1] == "plan_finished"


@pytest.mark.parametrize(
    ("returncodes", "expected_completed"),
    [
        ({"lint": 1, "unit": 0, "e2e": 0}, 1),
        ({"lint": 0, "unit": 1, "e2e": 0}, 2),
        ({"lint": 0, "unit": 0, "e2e": 1}, 3),
    ],
    ids=["first-stage-fails", "middle-stage-fails", "last-stage-fails"],
)
def test_fail_fast_completed_stages_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncodes: dict[str, int],
    expected_completed: int,
) -> None:
    plan = _plan("lint", "unit", "e2e")
    executed = _stub_run_stage(monkeypatch, returncodes)

    result = orchestrator.run_plan(
        plan,
        renderer=_RecordingRenderer(),
        artifacts_root=tmp_path,
        persist=False,
        fail_fast=True,
    )

    assert len(executed) == expected_completed
    assert len(result.stages) == expected_completed

    events = _read_events(tmp_path, plan.name)
    aborted = [e for e in events if e["event"] == "plan_aborted"]
    assert len(aborted) == 1
    assert aborted[0]["completed_stages"] == expected_completed
