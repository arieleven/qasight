"""Flag behaviour tests for ``qasight run`` (QA Strategies CLI rows).

Note: ``--tag``, ``--fail-fast``, ``--dry-run`` and ``--reporter`` are roadmap
items, not implemented flags — see the correction note in
``docs/CLI Commands/Command Reference.md``.  This module covers the flags that
exist: ``--workers``, ``--stream``, ``--force``, ``--no-persist``,
``--cycle all``, plus renderer selection and the trigger snapshot hand-off.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli import runner as cli_runner_mod
from qasight.cli.app import app
from qasight.cli.ui.console import make_console
from qasight.cli.ui.renderers import BufferedRenderer, CIRenderer, StreamRenderer
from qasight.config.triggers import TriggerResult
from qasight.services import cycle_run as cycle_run_mod
from tests.fixtures.engine import (
    stage_spec,
    use_echo_adapter,
    write_cycles_config,
    write_minimal_config,
    write_multi_stage_config,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _activated_trigger(**overrides: object) -> TriggerResult:
    values: dict[str, object] = {
        "stimulus": True,
        "reason": "changes detected",
        "matched_paths": ("src/app.py",),
        "mode": "snapshot",
        "persist_snapshot_after_run": True,
    }
    values.update(overrides)
    return TriggerResult(**values)  # type: ignore[arg-type]


def test_workers_flag_overrides_every_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    adapter = use_echo_adapter(monkeypatch)
    cfg = write_multi_stage_config(
        tmp_path,
        stages=[
            stage_spec("one", args=["--text", "1"], workers=2),
            stage_spec("two", args=["--text", "2"], workers=3),
        ],
    )

    result = cli_runner.invoke(
        app, ["run", "--cycle", "multi", "--config", str(cfg), "--workers", "7", "--no-report-db"]
    )

    assert result.exit_code == 0
    assert [c["workers"] for c in adapter.calls] == [7, 7]


def test_workers_default_comes_from_stage_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    adapter = use_echo_adapter(monkeypatch)
    cfg = write_multi_stage_config(
        tmp_path, stages=[stage_spec("one", args=["--text", "1"], workers=2)]
    )

    result = cli_runner.invoke(
        app, ["run", "--cycle", "multi", "--config", str(cfg), "--no-report-db"]
    )

    assert result.exit_code == 0
    assert [c["workers"] for c in adapter.calls] == [2]


def test_stream_flag_runs_clean_and_shows_stage_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path, args=("--text", "live-stream-line"))

    result = cli_runner.invoke(
        app, ["run", "--cycle", "smoke", "--config", str(cfg), "--stream", "--no-report-db"]
    )

    assert result.exit_code == 0
    assert "live-stream-line" in result.output


def test_pick_renderer_maps_flags_to_renderer_classes() -> None:
    console = make_console(plain=True)
    assert isinstance(
        cli_runner_mod._pick_renderer(console=console, stream=False, ci=False), BufferedRenderer
    )
    assert isinstance(
        cli_runner_mod._pick_renderer(console=console, stream=True, ci=False), StreamRenderer
    )
    # --ci wins over --stream: NDJSON must stay machine-readable.
    assert isinstance(
        cli_runner_mod._pick_renderer(console=console, stream=True, ci=True), CIRenderer
    )


def test_cycle_all_runs_every_cycle_in_sorted_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    adapter = use_echo_adapter(monkeypatch)
    cfg = write_cycles_config(
        tmp_path,
        cycles={
            "zeta": [stage_spec("z-stage", args=["--text", "z"])],
            "alpha": [stage_spec("a-stage", args=["--text", "a"])],
        },
    )

    result = cli_runner.invoke(
        app, ["run", "--cycle", "all", "--config", str(cfg), "--no-report-db"]
    )

    assert result.exit_code == 0
    assert [c["stage_args"][1] for c in adapter.calls] == ["a", "z"]
    assert (tmp_path / "artifacts" / "alpha" / "events.ndjson").is_file()
    assert (tmp_path / "artifacts" / "zeta" / "events.ndjson").is_file()


def test_force_bypasses_resting_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    adapter = use_echo_adapter(monkeypatch)
    monkeypatch.setattr(
        cycle_run_mod,
        "evaluate_cycle_trigger",
        lambda *, plan, cfg: _activated_trigger(
            stimulus=False, reason="no changes", matched_paths=()
        ),
    )
    cfg = write_cycles_config(
        tmp_path,
        cycles={"gated": [stage_spec("g", args=["--text", "forced"])]},
        trigger_paths={"gated": ["src/**"]},
    )
    base_args = ["run", "--cycle", "gated", "--config", str(cfg), "--no-report-db"]

    skipped = cli_runner.invoke(app, base_args)
    assert skipped.exit_code == 0
    assert adapter.calls == []

    forced = cli_runner.invoke(app, [*base_args, "--force"])
    assert forced.exit_code == 0
    assert len(adapter.calls) == 1


def test_no_persist_skips_plan_result_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = cli_runner.invoke(
        app, ["run", "--cycle", "smoke", "--config", str(cfg), "--no-persist", "--no-report-db"]
    )

    assert result.exit_code == 0
    assert (tmp_path / "artifacts" / "smoke" / "events.ndjson").is_file()
    assert not (tmp_path / "artifacts" / "smoke" / "plan_result.json").exists()


def test_default_persist_writes_plan_result_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = cli_runner.invoke(
        app, ["run", "--cycle", "smoke", "--config", str(cfg), "--no-report-db"]
    )

    assert result.exit_code == 0
    assert (tmp_path / "artifacts" / "smoke" / "plan_result.json").is_file()


def test_trigger_snapshot_persisted_only_after_successful_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    # LC-08: snapshot state advances only when the cycle both fired and passed.
    use_echo_adapter(monkeypatch)
    monkeypatch.setattr(
        cycle_run_mod, "evaluate_cycle_trigger", lambda *, plan, cfg: _activated_trigger()
    )
    snapshots: list[str] = []
    monkeypatch.setattr(
        cycle_run_mod,
        "persist_trigger_snapshot",
        lambda *, cfg, plan_name, anchor, patterns: snapshots.append(plan_name),
    )
    cfg = write_cycles_config(
        tmp_path,
        cycles={
            "gated-pass": [stage_spec("ok", args=["--text", "ok"])],
            "gated-fail": [stage_spec("bad", args=["--exit-code", "1"])],
        },
        trigger_paths={"gated-pass": ["src/**"], "gated-fail": ["src/**"]},
    )

    ok = cli_runner.invoke(
        app, ["run", "--cycle", "gated-pass", "--config", str(cfg), "--no-report-db"]
    )
    bad = cli_runner.invoke(
        app, ["run", "--cycle", "gated-fail", "--config", str(cfg), "--no-report-db"]
    )

    assert ok.exit_code == 0
    assert bad.exit_code == 1
    assert snapshots == ["gated-pass"]


def test_unknown_flag_is_a_usage_error(cli_runner: CliRunner, tmp_path: Path) -> None:
    cfg = write_minimal_config(tmp_path)
    result = cli_runner.invoke(app, ["run", "--cycle", "smoke", "--config", str(cfg), "--tag", "x"])
    assert result.exit_code == 2  # roadmap flag: rejected as CLI usage error today
