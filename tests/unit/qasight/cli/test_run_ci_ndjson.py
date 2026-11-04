"""``qasight run --ci`` NDJSON stdout tests (QA Strategies section D).

Locks the event schema in ``docs/CLI Commands/Troubleshooting and Error
Codes.md``: stdout is pure NDJSON, ``CIRenderer`` omits ``error`` on
``stage_finished``, and the artifact ``events.ndjson`` mirror carries the full
field set.  ``plan_aborted`` and ``dry_run`` events have no CLI surface yet
(no ``--fail-fast`` / ``--dry-run`` flags); engine-level ``plan_aborted``
coverage lives in ``tests/unit/qasight/engine/test_orchestrator.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.triggers import TriggerResult
from qasight.services import cycle_run as cycle_run_mod
from tests.fixtures.engine import (
    HangAdapter,
    assert_ndjson_events,
    parse_ndjson,
    read_artifact_events,
    stage_spec,
    use_adapter,
    use_echo_adapter,
    write_cycles_config,
    write_minimal_config,
    write_multi_stage_config,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _invoke_ci(cli_runner: CliRunner, *args: str) -> object:
    return cli_runner.invoke(app, ["run", "--ci", "--no-report-db", *args])


def test_ci_stdout_is_pure_ndjson_for_a_passing_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = _invoke_ci(cli_runner, "--cycle", "smoke", "--config", str(cfg))

    assert result.exit_code == 0
    events = parse_ndjson(result.output)  # raises if any line is not JSON
    assert_ndjson_events(
        events, ["plan_started", "stage_started", "stage_finished", "plan_finished"]
    )

    assert events[0] == {"event": "plan_started", "plan": "smoke", "stage_count": 1}
    started = events[1]
    assert started["stage"] == "smoke-stage"
    assert (started["index"], started["count"]) == (1, 1)

    finished = events[2]
    assert finished["returncode"] == 0
    assert finished["timed_out"] is False
    assert finished["log_path"].endswith("run.log")

    plan_finished = events[3]
    assert plan_finished["exit_code"] == 0
    assert plan_finished["aggregate_returncode"] == 0
    assert plan_finished["stages"][0]["stage"] == "smoke-stage"


def test_ci_failing_stage_reports_returncode_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_multi_stage_config(
        tmp_path, stages=[stage_spec("fails", args=["--exit-code", "1", "--text", "bad"])]
    )

    result = _invoke_ci(cli_runner, "--cycle", "multi", "--config", str(cfg))

    assert result.exit_code == 1
    events = parse_ndjson(result.output)
    finished = [e for e in events if e["event"] == "stage_finished"][0]
    assert finished["returncode"] == 1
    assert events[-1]["exit_code"] == 1


def test_ci_config_error_emits_error_event(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _invoke_ci(cli_runner, "--cycle", "smoke", "--config", str(tmp_path / "missing.yaml"))

    assert result.exit_code == 2
    events = parse_ndjson(result.output)
    assert len(events) == 1
    assert events[0]["event"] == "error"
    assert events[0]["code"] == "invalid_input"
    assert "missing.yaml" in events[0]["message"]


def test_ci_unknown_cycle_emits_error_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = _invoke_ci(cli_runner, "--cycle", "ghost", "--config", str(cfg))

    assert result.exit_code == 2
    events = parse_ndjson(result.output)
    assert events[0]["event"] == "error"
    assert events[0]["code"] == "invalid_input"


def test_ci_cycle_trigger_resting_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    monkeypatch.setattr(
        cycle_run_mod,
        "evaluate_cycle_trigger",
        lambda *, plan, cfg: TriggerResult(
            stimulus=False,
            reason="no matching changes",
            matched_paths=(),
            mode="git",
            persist_snapshot_after_run=False,
        ),
    )
    cfg = write_cycles_config(
        tmp_path,
        cycles={"gated": [stage_spec("g", args=["--text", "g"])]},
        trigger_paths={"gated": ["src/**"]},
    )

    result = _invoke_ci(cli_runner, "--cycle", "gated", "--config", str(cfg))

    assert result.exit_code == 0
    events = parse_ndjson(result.output)
    assert_ndjson_events(events, ["cycle_trigger"])  # resting: no plan events at all
    assert events[0] == {
        "event": "cycle_trigger",
        "cycle": "gated",
        "status": "resting",
        "reason": "no matching changes",
        "matched": [],
        "mode": "git",
    }


def test_ci_cycle_trigger_activated_precedes_plan_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    monkeypatch.setattr(
        cycle_run_mod,
        "evaluate_cycle_trigger",
        lambda *, plan, cfg: TriggerResult(
            stimulus=True,
            reason="src changed",
            matched_paths=("src/app.py",),
            mode="git",
            persist_snapshot_after_run=False,
        ),
    )
    cfg = write_cycles_config(
        tmp_path,
        cycles={"gated": [stage_spec("g", args=["--text", "g"])]},
        trigger_paths={"gated": ["src/**"]},
    )

    result = _invoke_ci(cli_runner, "--cycle", "gated", "--config", str(cfg))

    assert result.exit_code == 0
    events = parse_ndjson(result.output)
    assert_ndjson_events(
        events,
        ["cycle_trigger", "plan_started", "stage_started", "stage_finished", "plan_finished"],
    )
    assert events[0]["status"] == "activated"
    assert events[0]["matched"] == ["src/app.py"]


def test_ci_timeout_stdout_omits_error_but_artifact_mirror_has_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    # Section D row: `error` on timeout lives in the artifact mirror; the
    # stdout stage_finished only carries timed_out + returncode 124.
    use_adapter(monkeypatch, HangAdapter())
    cfg = write_minimal_config(tmp_path, timeout_s=0.5)

    result = _invoke_ci(cli_runner, "--cycle", "smoke", "--config", str(cfg))

    assert result.exit_code == 3
    stdout_finished = [e for e in parse_ndjson(result.output) if e["event"] == "stage_finished"][0]
    assert stdout_finished["timed_out"] is True
    assert stdout_finished["returncode"] == 124
    assert "error" not in stdout_finished

    mirror_finished = [
        e
        for e in read_artifact_events(tmp_path / "artifacts", "smoke")
        if e["event"] == "stage_finished"
    ][0]
    assert mirror_finished["timed_out"] is True
    assert mirror_finished["returncode"] == 124
    assert mirror_finished["error"] is not None and "timeout_s" in mirror_finished["error"]
