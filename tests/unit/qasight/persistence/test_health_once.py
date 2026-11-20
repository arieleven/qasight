"""The JSON and DB backends share one parse of a run's results (``compute_stage_health``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.engine.exit_codes import EngineExitCode
from qasight.engine.result import PlanResult, StageResult
from qasight.persistence import health

pytestmark = pytest.mark.tier_fast


def _result(tmp_path: Path) -> PlanResult:
    stage_dir = tmp_path / "plan" / "unit"
    folder = stage_dir / "allure-results" / "pytest"
    folder.mkdir(parents=True)
    for i, status in enumerate(["passed", "failed"]):
        (folder / f"{i}-result.json").write_text(
            json.dumps({"name": f"t{i}", "status": status, "start": 1, "stop": 5}),
            encoding="utf-8",
        )
    stage = StageResult(
        stage_name="unit",
        framework="pytest",
        returncode=1,
        started_at=0.0,
        finished_at=1.0,
        duration_s=1.0,
        log_path=None,
        artifacts_dir=stage_dir,
        command=("pytest",),
        output_tail="",
    )
    return PlanResult(
        plan_name="plan",
        started_at=0.0,
        finished_at=1.0,
        duration_s=1.0,
        stages=(stage,),
        aggregate_returncode=1,
        exit_code=EngineExitCode(1),
    )


def test_the_same_run_is_parsed_once_and_each_caller_gets_its_own_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _result(tmp_path)
    calls: list[int] = []
    real = health.parse_collected_results
    monkeypatch.setattr(health, "parse_collected_results", lambda c: calls.append(1) or real(c))
    first, pct = health.compute_stage_health(result, tmp_path)
    first[0]["passed"] = 99  # a backend merging rows into its own metadata
    second, pct2 = health.compute_stage_health(result, tmp_path)
    assert calls == [1]
    assert second[0]["passed"] == 1 and second[0]["failed"] == 1
    assert pct == pct2 == 50.0
    health.compute_stage_health(_result(tmp_path / "other"), tmp_path / "other")
    assert calls == [1, 1]  # another run is parsed on its own
