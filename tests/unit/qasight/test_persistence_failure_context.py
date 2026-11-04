"""Failure evidence stored on engine runs (feeds the AI failure analysis)."""

from __future__ import annotations

import json
from pathlib import Path

from qasight.engine.exit_codes import EngineExitCode
from qasight.engine.result import PlanResult, StageResult
from qasight.persistence.failure_context import failed_cases_from_allure, failure_metadata


def _stage(
    tmp_path: Path, name: str, *, returncode: int, output_tail: str = "", timed_out: bool = False
) -> StageResult:
    return StageResult(
        stage_name=name,
        framework="pytest",
        returncode=returncode,
        started_at=0.0,
        finished_at=1.0,
        duration_s=1.0,
        log_path=None,
        artifacts_dir=tmp_path / name,
        command=("pytest",),
        output_tail=output_tail,
        timed_out=timed_out,
    )


def _plan(*stages: StageResult, exit_code: EngineExitCode) -> PlanResult:
    return PlanResult(
        plan_name="smoke",
        started_at=0.0,
        finished_at=1.0,
        duration_s=1.0,
        stages=stages,
        aggregate_returncode=max(s.returncode for s in stages),
        exit_code=exit_code,
    )


def _write_result(results_dir: Path, filename: str, payload: dict) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_failed_cases_from_allure_collects_failed_and_broken(tmp_path: Path) -> None:
    _write_result(
        tmp_path,
        "a-result.json",
        {
            "name": "test_auth_expiry",
            "fullName": "tests.auth.test_auth_expiry",
            "status": "failed",
            "statusDetails": {
                "message": "API failed: token expired",
                "trace": "Traceback: AuthError",
            },
        },
    )
    _write_result(tmp_path, "b-result.json", {"name": "test_ok", "status": "passed"})
    (tmp_path / "c-result.json").write_text("{not json", encoding="utf-8")

    cases, trace = failed_cases_from_allure(results_dir=tmp_path)

    assert [c["name"] for c in cases] == ["test_auth_expiry"]
    assert "token expired" in cases[0]["message"]
    assert trace is not None and "AuthError" in trace


def test_failure_metadata_is_empty_for_a_passing_plan(tmp_path: Path) -> None:
    assert (
        failure_metadata(
            _plan(_stage(tmp_path, "s", returncode=0), exit_code=EngineExitCode.SUCCESS)
        )
        == {}
    )


def test_failure_metadata_collects_cases_across_stages_and_the_failing_log_tail(
    tmp_path: Path,
) -> None:
    _write_result(
        tmp_path / "unit" / "allure-results" / "pytest",
        "x-result.json",
        {
            "name": "test_math",
            "status": "failed",
            "statusDetails": {"message": "assert 1 == 2", "trace": "E assert"},
        },
    )
    result = _plan(
        _stage(tmp_path, "lint", returncode=0),
        _stage(tmp_path, "unit", returncode=1, output_tail="FAILED test_math\n1 failed"),
        exit_code=EngineExitCode.DOMAIN_FAILURE,
    )

    md = failure_metadata(result)

    assert md["failure_context"]["captured_cases"] == 1
    assert md["failure_context"]["failed_cases"][0]["stage"] == "unit"
    assert md["error_message"] == "assert 1 == 2"
    assert md["traceback"] == "E assert"
    assert md["log_tail"].endswith("1 failed")


def test_failure_metadata_falls_back_to_output_tail_and_flags_timeouts(tmp_path: Path) -> None:
    result = _plan(
        _stage(
            tmp_path,
            "slow",
            returncode=124,
            output_tail="still running... token=abcdefghijklmnop",
            timed_out=True,
        ),
        exit_code=EngineExitCode.DOMAIN_FAILURE,
    )

    md = failure_metadata(result)

    assert "failure_context" not in md
    assert md["error"] == "timeout"
    assert md["error_message"] == md["log_tail"]
    assert md["log_tail"].startswith("still running")
    assert "abcdefghijklmnop" not in md["log_tail"]  # redacted before it is stored


def test_failure_metadata_names_a_crashed_stage_first(tmp_path: Path) -> None:
    results = tmp_path / "unit" / "allure-results" / "pytest"
    _write_result(
        results,
        "a-result.json",
        {
            "name": "test_deep",
            "status": "broken",
            "statusDetails": {"message": "boom", "trace": "java.lang.StackOverflowError\n\tat A.a"},
        },
    )
    tested = _stage(tmp_path, "unit", returncode=1)
    crashed = StageResult(
        **{
            **vars(_stage(tmp_path, "native", returncode=-11)),
            "crash_kind": "crash",
            "crash_detail": "Crashed: process killed by SIGSEGV (exit -11)",
        }
    )

    cases, _ = failed_cases_from_allure(results_dir=results)
    assert cases[0]["crash"] == "Stack overflow (Java/Kotlin): java.lang.StackOverflowError"
    out = failure_metadata(_plan(tested, crashed, exit_code=EngineExitCode.DOMAIN_FAILURE))
    assert out["crash"] == "Crashed: process killed by SIGSEGV (exit -11)"
    # Without a crashed stage, the first crashed test is the evidence.
    out = failure_metadata(_plan(tested, exit_code=EngineExitCode.DOMAIN_FAILURE))
    assert out["crash"].startswith("Stack overflow (Java/Kotlin)")
