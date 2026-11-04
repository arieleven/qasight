"""Unit tests for ``run_stage`` (QA Strategies EC-03a/EC-03b + ST rows).

Real subprocesses are used — ``tests/fixtures/engine/scripts/echo.py`` is the
"framework" — so the pipe/reader-thread/timeout machinery is exercised for
real, just very fast.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from qasight.config.schema import Stage
from qasight.engine.executor import run_stage
from tests.fixtures.engine import (
    HangAdapter,
    MissingBinaryAdapter,
    use_adapter,
    use_echo_adapter,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _stage(
    name: str = "echo-stage",
    *,
    args: tuple[str, ...] = (),
    timeout_s: float | None = 30.0,
    extra_env: tuple[tuple[str, str], ...] = (),
    workers: int = 4,
) -> Stage:
    return Stage(
        name=name,
        framework="pytest",
        target_repo=Path("."),
        args=args,
        workers=workers,
        timeout_s=timeout_s,
        extra_env=extra_env,
    )


def test_passing_stage_result_and_artifact_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = use_echo_adapter(monkeypatch)
    stage = _stage(args=("--text", "hello-executor"), workers=7)

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert result.returncode == 0
    assert result.timed_out is False
    assert result.error is None
    assert result.stage_name == "echo-stage"
    assert result.target_repo == Path.cwd().resolve()
    assert "hello-executor" in result.output_tail
    assert result.duration_s >= 0.0

    # ST: durable log under <artifacts>/<plan>/<stage>/run.log.
    log = tmp_path / "demo" / "echo-stage" / "run.log"
    assert result.log_path == log.resolve()
    assert "hello-executor" in log.read_text(encoding="utf-8")

    # Adapter contract: resolved results dir + workers forwarded.
    call = adapter.calls[0]
    assert call["workers"] == 7
    assert (
        call["results_dir"]
        == (tmp_path / "demo" / "echo-stage" / "allure-results" / "echo").resolve()
    )
    assert call["results_dir"].is_dir()


def test_failing_stage_propagates_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    stage = _stage(args=("--exit-code", "3", "--text", "boom"))

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert result.returncode == 3
    assert result.timed_out is False
    assert "boom" in result.output_tail


def test_failing_stage_names_a_stack_overflow_in_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    overflow = "RecursionError: maximum recursion depth exceeded"
    stage = _stage(args=("--exit-code", "1", "--stderr-text", overflow))

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert result.crash_kind == "stack_overflow"
    assert result.crash_detail == f"Stack overflow (Python): {overflow}"
    assert result.log_path is not None
    assert "[qasight] crash detection: Stack overflow (Python)" in result.log_path.read_text()


def test_passing_stage_is_never_flagged_as_crashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    stage = _stage(args=("--text", "RecursionError: maximum recursion depth exceeded"))

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert result.returncode == 0
    assert result.crash_kind is None
    assert result.crash_detail is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_stage_killed_by_sigsegv_is_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    stage = _stage(args=("--kill-with", "SIGSEGV"))

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert result.returncode != 0
    assert result.crash_kind == "crash"
    assert result.crash_detail is not None and "SIGSEGV" in result.crash_detail


def test_stderr_is_merged_into_stdout_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_echo_adapter(monkeypatch)
    stage = _stage(args=("--text", "out-line", "--stderr-text", "err-line"))

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert "out-line" in result.output_tail
    assert "err-line" in result.output_tail  # deterministic ordering contract


def test_allure_results_dir_is_wiped_between_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    stale = tmp_path / "demo" / "echo-stage" / "allure-results" / "echo" / "stale.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")

    run_stage(_stage(), plan_name="demo", artifacts_root=tmp_path)

    assert stale.parent.is_dir()
    assert not stale.exists()


def test_stage_env_carries_extra_env_and_testo_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    stage = _stage(
        args=(
            "--print-env",
            "STAGE_TOKEN",
            "--print-env",
            "TESTO_LAST_TEST_TYPE",
            "--print-env",
            "TESTO_SHARED_ALLURE_RESULTS_DIR",
        ),
        extra_env=(("STAGE_TOKEN", "s3cr3t"),),
    )

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert "STAGE_TOKEN=s3cr3t" in result.output_tail
    assert "TESTO_LAST_TEST_TYPE=pytest" in result.output_tail
    results_dir = (tmp_path / "demo" / "echo-stage" / "allure-results" / "echo").resolve()
    assert f"TESTO_SHARED_ALLURE_RESULTS_DIR={results_dir}" in result.output_tail


def test_parent_env_seam_replaces_os_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    stage = _stage(args=("--print-env", "FROM_PARENT_ONLY"))
    assert "FROM_PARENT_ONLY" not in os.environ
    parent_env = dict(os.environ) | {"FROM_PARENT_ONLY": "yes"}

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path, parent_env=parent_env)

    assert "FROM_PARENT_ONLY=yes" in result.output_tail


def test_on_chunk_streams_live_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_echo_adapter(monkeypatch)
    chunks: list[bytes] = []

    run_stage(
        _stage(args=("--text", "streamed-bytes")),
        plan_name="demo",
        artifacts_root=tmp_path,
        on_chunk=chunks.append,
    )

    assert b"streamed-bytes" in b"".join(chunks)


def test_missing_binary_returns_127_with_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # EC-03a: FileNotFoundError from Popen is normalised to rc 127.
    use_adapter(monkeypatch, MissingBinaryAdapter())

    result = run_stage(_stage(name="missing-stage"), plan_name="demo", artifacts_root=tmp_path)

    assert result.returncode == 127
    assert result.timed_out is False
    assert result.error is not None and "executable not found" in result.error
    assert result.command == ("testo-missing-binary-9c2f4e",)
    assert result.target_repo == Path.cwd().resolve()


def test_adapter_failure_retains_resolved_target_repo(tmp_path: Path) -> None:
    stage = Stage(name="invalid-command", framework="command", target_repo=tmp_path)

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert result.returncode == 4
    assert result.internal_failure is True
    assert "needs args" in (result.error or "")
    assert result.target_repo == tmp_path.resolve()


def test_stage_result_records_the_stage_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The run record keeps the tier the stage ran with, so the test pyramid
    # doesn't depend on whatever qasight.yaml says later.
    use_echo_adapter(monkeypatch)
    stage = Stage(name="flows", framework="pytest", target_repo=Path("."), tier="e2e")

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert result.tier == "e2e"


def test_timeout_kills_stage_and_returns_124(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # EC-03b: documented timeout contract — rc 124 + timed_out, whatever
    # signal actually reaped the process.
    use_adapter(monkeypatch, HangAdapter())

    result = run_stage(
        _stage(name="hang-stage", timeout_s=0.5),
        plan_name="demo",
        artifacts_root=tmp_path,
    )

    assert result.timed_out is True
    assert result.returncode == 124
    assert result.error is not None and "timeout_s" in result.error
    assert "hanging" in result.output_tail  # pre-timeout output was captured


def test_behavex_report_failure_sets_stage_error_not_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.reporting import native_reports

    def broken_report(_stage_dir: Path, *, raise_errors: bool = False) -> None:
        raise RuntimeError("report.json is truncated")

    use_echo_adapter(monkeypatch)
    monkeypatch.setattr(native_reports, "ensure_behavex_report_html", broken_report)
    stage = dataclasses.replace(_stage(args=("--text", "ok")), framework="behavex")

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert result.returncode == 0
    assert result.error == "BehaveX HTML report generation failed: report.json is truncated"
    assert result.error in (tmp_path / "demo" / "echo-stage" / "run.log").read_text()


def test_behavex_allure_export_failure_sets_stage_error_not_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.reporting import behavex_allure

    def broken_export(_report_dir: Path, _results_dir: Path) -> int:
        raise RuntimeError("formatter crashed")

    use_echo_adapter(monkeypatch)
    monkeypatch.setattr(behavex_allure, "export_behavex_allure_results", broken_export)
    stage = dataclasses.replace(_stage(args=("--text", "ok")), framework="behavex")

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path)

    assert result.returncode == 0
    assert result.error == "BehaveX Allure export failed: formatter crashed"
    assert result.error in (tmp_path / "demo" / "echo-stage" / "run.log").read_text()


def test_command_records_full_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_echo_adapter(monkeypatch)
    result = run_stage(
        _stage(args=("--text", "argv-check")), plan_name="demo", artifacts_root=tmp_path
    )
    assert result.command[-2:] == ("--text", "argv-check")
    assert "echo.py" in result.command[1]


class _MissingBehaveAdapter(MissingBinaryAdapter):
    def build_argv(self, **_: object) -> list[str]:
        return ["behave", "features/smoke.feature"]


def test_missing_behave_names_the_bdd_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A plain ``pip install qasight`` has no Behave; the error says how to get it.
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    use_adapter(monkeypatch, _MissingBehaveAdapter())

    result = run_stage(_stage(name="flows"), plan_name="demo", artifacts_root=tmp_path)

    assert result.returncode == 127
    assert result.error is not None and "qasight[bdd]" in result.error


def test_missing_target_repo_names_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Popen raises FileNotFoundError for a missing cwd too; the error must not blame the binary.
    use_echo_adapter(monkeypatch)
    missing = tmp_path / "no-such-repo"
    stage = dataclasses.replace(_stage(name="no-repo"), target_repo=missing)

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path / "artifacts")

    assert result.returncode == 127
    assert result.error is not None
    assert "target_repo is not a folder that exists" in result.error
    assert str(missing) in result.error


def test_run_log_holds_only_this_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_echo_adapter(monkeypatch)
    first = run_stage(
        _stage(args=("--text", "first-run")), plan_name="demo", artifacts_root=tmp_path
    )
    second = run_stage(
        _stage(args=("--text", "second-run")), plan_name="demo", artifacts_root=tmp_path
    )
    assert first.log_path == second.log_path
    text = second.log_path.read_text()  # type: ignore[union-attr]
    assert "second-run" in text
    assert "first-run" not in text
