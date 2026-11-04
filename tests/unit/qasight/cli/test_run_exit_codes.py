"""End-to-end exit-code tests for ``qasight run`` (QA Strategies EC rows).

Each test drives the real CLI → runner → orchestrator → executor path via
Typer's ``CliRunner``; stages are real ``echo.py`` subprocesses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.triggers import TriggerResult
from qasight.engine import orchestrator
from tests.fixtures.engine import (
    HangAdapter,
    MissingBinaryAdapter,
    stage_spec,
    use_adapter,
    use_echo_adapter,
    write_cycles_config,
    write_minimal_config,
    write_multi_stage_config,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _run(cli_runner: CliRunner, *args: str) -> object:
    return cli_runner.invoke(app, ["run", *args, "--no-report-db"])


def test_ec00_all_stages_pass_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = _run(cli_runner, "--cycle", "smoke", "--config", str(cfg))

    assert result.exit_code == 0


def test_ec01_framework_failure_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_multi_stage_config(
        tmp_path,
        stages=[
            stage_spec("passes", args=["--text", "ok"]),
            stage_spec("fails", args=["--exit-code", "1", "--text", "nope"]),
        ],
    )

    result = _run(cli_runner, "--cycle", "multi", "--config", str(cfg))

    assert result.exit_code == 1


def test_ec02_missing_config_file_exits_2(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _run(cli_runner, "--cycle", "smoke", "--config", str(tmp_path / "nope.yaml"))
    assert result.exit_code == 2


def test_ec02_unknown_cycle_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = _run(cli_runner, "--cycle", "does-not-exist", "--config", str(cfg))

    assert result.exit_code == 2


def test_ec02_invalid_yaml_schema_exits_2(cli_runner: CliRunner, tmp_path: Path) -> None:
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text("version: 1\ncycles: {}\n", encoding="utf-8")

    result = _run(cli_runner, "--cycle", "smoke", "--config", str(cfg))

    assert result.exit_code == 2


def test_cli01_missing_cycle_with_multiple_cycles_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_cycles_config(
        tmp_path,
        cycles={
            "alpha": [stage_spec("a", args=["--text", "a"])],
            "beta": [stage_spec("b", args=["--text", "b"])],
        },
    )

    result = _run(cli_runner, "--config", str(cfg))

    assert result.exit_code == 2


def test_missing_cycle_with_single_cycle_runs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = _run(cli_runner, "--config", str(cfg))

    assert result.exit_code == 0
    assert (tmp_path / "artifacts" / "smoke" / "events.ndjson").is_file()


def test_ec03a_missing_binary_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_adapter(monkeypatch, MissingBinaryAdapter())
    cfg = write_minimal_config(tmp_path)

    result = _run(cli_runner, "--cycle", "smoke", "--config", str(cfg))

    assert result.exit_code == 3


def test_ec03b_stage_timeout_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_adapter(monkeypatch, HangAdapter())
    cfg = write_minimal_config(tmp_path, timeout_s=0.5)

    result = _run(cli_runner, "--cycle", "smoke", "--config", str(cfg))

    assert result.exit_code == 3


def test_ec04_engine_internal_failure_exits_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    def exploding_run_stage(*_: object, **__: object) -> object:
        raise RuntimeError("engine internal explosion")

    monkeypatch.setattr(orchestrator, "run_stage", exploding_run_stage)
    cfg = write_minimal_config(tmp_path)

    result = _run(cli_runner, "--cycle", "smoke", "--config", str(cfg))

    assert result.exit_code == 4


def test_ec05_trigger_resting_exits_0_without_running_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    from qasight.services import cycle_run as cycle_run_mod

    adapter = use_echo_adapter(monkeypatch)
    monkeypatch.setattr(
        cycle_run_mod,
        "evaluate_cycle_trigger",
        lambda *, plan, cfg: TriggerResult(
            stimulus=False,
            reason="no matching changes",
            matched_paths=(),
            mode="snapshot",
            persist_snapshot_after_run=False,
        ),
    )
    cfg = write_cycles_config(
        tmp_path,
        cycles={"gated": [stage_spec("g", args=["--text", "gated"])]},
        trigger_paths={"gated": ["src/**"]},
    )

    result = _run(cli_runner, "--cycle", "gated", "--config", str(cfg))

    assert result.exit_code == 0
    assert adapter.calls == []  # resting cycles never reach the executor


def test_cycle_all_returns_worst_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_cycles_config(
        tmp_path,
        cycles={
            "a-pass": [stage_spec("a", args=["--text", "a-ok"])],
            "b-fail": [stage_spec("b", args=["--exit-code", "1", "--text", "b-bad"])],
        },
    )

    result = _run(cli_runner, "--cycle", "all", "--config", str(cfg))

    assert result.exit_code == 1
