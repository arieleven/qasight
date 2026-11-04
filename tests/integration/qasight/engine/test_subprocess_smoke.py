"""Integration smoke: real subprocesses through the whole stack (INT-01/INT-02).

No Docker, no DB — the "framework" is ``tests/fixtures/engine/scripts/echo.py``
launched as a genuine subprocess, so the pipe/reader-thread/artifact plumbing
runs unstubbed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.schema import Plan, Stage
from qasight.engine.exit_codes import EngineExitCode
from qasight.engine.orchestrator import run_plan
from tests.fixtures.engine import (
    NoopRenderer,
    assert_ndjson_events,
    parse_ndjson,
    read_artifact_events,
    stage_spec,
    use_echo_adapter,
    write_multi_stage_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.tier_heavy]


def test_int01_run_plan_executes_two_real_subprocess_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    plan = Plan(
        name="smoke-plan",
        description=None,
        stages=(
            Stage(
                name="first",
                framework="pytest",
                target_repo=Path("."),
                args=("--text", "first-stage-output"),
            ),
            Stage(
                name="second",
                framework="pytest",
                target_repo=Path("."),
                args=("--text", "second-stage-output"),
            ),
        ),
    )

    result = run_plan(plan, renderer=NoopRenderer(), artifacts_root=tmp_path, persist=False)

    assert result.exit_code is EngineExitCode.SUCCESS
    assert [s.returncode for s in result.stages] == [0, 0]
    assert "first-stage-output" in result.stages[0].output_tail
    assert "second-stage-output" in result.stages[1].output_tail

    # Durable artifacts written by the real executor.
    for stage_name, text in (("first", "first-stage-output"), ("second", "second-stage-output")):
        log = tmp_path / "smoke-plan" / stage_name / "run.log"
        assert text in log.read_text(encoding="utf-8")

    assert_ndjson_events(
        read_artifact_events(tmp_path, "smoke-plan"),
        [
            "plan_started",
            "stage_started",
            "stage_finished",
            "stage_started",
            "stage_finished",
            "plan_finished",
        ],
    )


def test_int02_full_testo_run_ci_with_echo_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'testo_history.db'}")
    cfg = write_multi_stage_config(
        tmp_path,
        cycle="ci-smoke",
        stages=[
            stage_spec("pass-stage", args=["--text", "ci-pass"]),
            stage_spec("fail-stage", args=["--exit-code", "1", "--text", "ci-fail"]),
        ],
    )

    result = CliRunner().invoke(
        app, ["run", "--cycle", "ci-smoke", "--config", str(cfg), "--ci", "--no-report-db"]
    )

    assert result.exit_code == 1
    events = parse_ndjson(result.output)
    assert_ndjson_events(
        events,
        [
            "plan_started",
            "stage_started",
            "stage_finished",
            "stage_started",
            "stage_finished",
            "plan_finished",
        ],
    )
    assert [e["returncode"] for e in events if e["event"] == "stage_finished"] == [0, 1]
    assert events[-1]["exit_code"] == 1

    # plan_result.json persisted by the default persistence stack.
    assert (tmp_path / "artifacts" / "ci-smoke" / "plan_result.json").is_file()
    # Real subprocess output reached the per-stage logs.
    assert "ci-fail" in (tmp_path / "artifacts" / "ci-smoke" / "fail-stage" / "run.log").read_text(
        encoding="utf-8"
    )
