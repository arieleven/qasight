"""``CycleRunService`` — the cycle use case shared by ``qasight run`` and the API."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import qasight.services.report_archive as report_archive
from qasight.config.loader import discover_and_load
from qasight.config.resolver import resolve_plan
from qasight.config.schema import Plan
from qasight.config.triggers import TriggerResult
from qasight.reporting.allure_results import parse_collected_results
from qasight.reporting.collector import collect_results
from qasight.services import cycle_run as cycle_run_mod
from qasight.services.cycle_run import CycleRunOptions, CycleRunService, NoStagesEnabledError
from tests.fixtures.engine import (
    NoopRenderer,
    stage_spec,
    use_echo_adapter,
    write_cycles_config,
    write_minimal_config,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


class _RecordingListener:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def trigger_evaluated(self, plan: Plan, result: TriggerResult) -> None:
        self.events.append(("trigger", result.stimulus))

    def trigger_bypassed(self, plan: Plan) -> None:
        self.events.append(("bypassed", plan.name))

    def report_archived(self, report_id: UUID | None, *, background: bool) -> None:
        self.events.append(("archived", report_id))


def _trigger(*, stimulus: bool) -> TriggerResult:
    return TriggerResult(
        stimulus=stimulus,
        reason="changes" if stimulus else "no changes",
        matched_paths=("src/app.py",) if stimulus else (),
        mode="snapshot",
        persist_snapshot_after_run=False,
    )


def _load(config: Path, cycle: str):  # noqa: ANN202
    cfg = discover_and_load(config_path=config)
    return cfg, resolve_plan(cfg, plan_name=cycle)


def test_runs_plan_and_returns_engine_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = use_echo_adapter(monkeypatch)
    cfg, plan = _load(write_minimal_config(tmp_path, args=("--exit-code", "1")), "smoke")

    outcome = CycleRunService().run(
        cfg=cfg, plan=plan, renderer=NoopRenderer(), options=CycleRunOptions(report_db=False)
    )

    assert outcome.exit_code == 1
    assert not outcome.skipped
    assert len(adapter.calls) == 1
    assert (tmp_path / "artifacts" / "smoke" / "plan_result.json").is_file()


def test_resting_trigger_skips_engine_and_tells_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = use_echo_adapter(monkeypatch)
    monkeypatch.setattr(
        cycle_run_mod, "evaluate_cycle_trigger", lambda *, plan, cfg: _trigger(stimulus=False)
    )
    config = write_cycles_config(
        tmp_path, cycles={"gated": [stage_spec("g")]}, trigger_paths={"gated": ["src/**"]}
    )
    cfg, plan = _load(config, "gated")
    listener = _RecordingListener()

    outcome = CycleRunService(listener=listener).run(cfg=cfg, plan=plan, renderer=NoopRenderer())

    assert outcome.skipped
    assert outcome.exit_code == 0
    assert adapter.calls == []
    assert listener.events == [("trigger", False)]


def test_force_bypasses_trigger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = use_echo_adapter(monkeypatch)
    config = write_cycles_config(
        tmp_path, cycles={"gated": [stage_spec("g")]}, trigger_paths={"gated": ["src/**"]}
    )
    cfg, plan = _load(config, "gated")
    listener = _RecordingListener()

    outcome = CycleRunService(listener=listener).run(
        cfg=cfg,
        plan=plan,
        renderer=NoopRenderer(),
        options=CycleRunOptions(force=True, report_db=False),
    )

    assert outcome.exit_code == 0
    assert len(adapter.calls) == 1
    assert listener.events == [("bypassed", "gated")]


def test_archives_report_and_reports_id_to_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    report_id = uuid4()
    calls: list[tuple[str, int | None]] = []

    def fake_archive(
        *, artifacts_root: Path, plan_name: str, exit_code_override: int | None = None
    ) -> UUID:
        calls.append((plan_name, exit_code_override))
        return report_id

    monkeypatch.setattr(report_archive, "try_persist_cycle_report", fake_archive)
    cfg, plan = _load(write_minimal_config(tmp_path), "smoke")
    listener = _RecordingListener()

    CycleRunService(listener=listener).run(cfg=cfg, plan=plan, renderer=NoopRenderer())

    assert calls == [("smoke", 0)]
    assert listener.events == [("archived", report_id)]


def test_no_persist_skips_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_echo_adapter(monkeypatch)
    monkeypatch.setattr(
        report_archive,
        "try_persist_cycle_report",
        lambda **_: pytest.fail("archive must not run with persist=False"),
    )
    cfg, plan = _load(write_minimal_config(tmp_path), "smoke")

    outcome = CycleRunService().run(
        cfg=cfg, plan=plan, renderer=NoopRenderer(), options=CycleRunOptions(persist=False)
    )

    assert outcome.exit_code == 0


def test_workers_override_applies_to_every_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    config = write_cycles_config(tmp_path, cycles={"multi": [stage_spec("a"), stage_spec("b")]})
    cfg, plan = _load(config, "multi")

    outcome = CycleRunService().run(
        cfg=cfg,
        plan=plan,
        renderer=NoopRenderer(),
        options=CycleRunOptions(workers_override=3, report_db=False),
    )

    assert [s.workers for s in outcome.plan.stages] == [3, 3]


_JUNIT_ONE_PASS_ONE_FAIL = textwrap.dedent(
    """\
    import pathlib, sys
    pathlib.Path("r").mkdir(exist_ok=True)
    pathlib.Path("r/junit.xml").write_text(
        '<testsuite name="s" tests="2">'
        '<testcase classname="s" name="ok"/>'
        '<testcase classname="s" name="bad"><failure message="boom"/></testcase>'
        "</testsuite>"
    )
    sys.exit(1)
    """
)


@pytest.mark.parametrize("workers_override", [None, 2])
def test_command_stage_junit_xml_counts_in_cycle_results(
    tmp_path: Path, workers_override: int | None
) -> None:
    """Regression: the resolver and ``--workers`` used to drop ``junit_xml``,
    so a ``command`` stage reported 0 tests."""
    script = tmp_path / "runner.py"
    script.write_text(_JUNIT_ONE_PASS_ONE_FAIL, encoding="utf-8")
    stage = {
        "name": "jest",
        "framework": "command",
        "args": [sys.executable, str(script)],
        "junit_xml": "r/*.xml",
        "tier": "e2e",
    }
    cfg, plan = _load(write_cycles_config(tmp_path, cycles={"app": [stage]}), "app")

    outcome = CycleRunService().run(
        cfg=cfg,
        plan=plan,
        renderer=NoopRenderer(),
        options=CycleRunOptions(workers_override=workers_override, persist=False, report_db=False),
    )

    assert outcome.exit_code != 0
    (ran,) = outcome.plan.stages
    assert (ran.tier, ran.junit_xml) == ("e2e", ("r/*.xml",))
    aggregate = parse_collected_results(collect_results(tmp_path / "artifacts", plan_name="app"))
    assert (aggregate.total, aggregate.passed, aggregate.failed) == (2, 1, 1)


def test_plan_with_no_enabled_stages_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    cfg, plan = _load(write_minimal_config(tmp_path), "smoke")
    monkeypatch.setattr(cycle_run_mod, "resolve_stages_for_plan", lambda _plan: ())

    with pytest.raises(NoStagesEnabledError):
        CycleRunService().run(cfg=cfg, plan=plan, renderer=NoopRenderer())


def test_single_stage_plan_wraps_one_framework_call(tmp_path: Path) -> None:
    plan = cycle_run_mod.single_stage_plan(
        framework="behave",
        target_repo=tmp_path,
        args=["features/smoke.feature"],
        timeout_s=30.0,
        extra_env={"B": "2", "A": "1"},
    )

    assert plan.name == "adhoc"
    assert plan.trigger is None
    (stage,) = plan.stages
    assert stage.framework == "behave"
    assert stage.target_repo == tmp_path.resolve()
    assert stage.args == ("features/smoke.feature",)
    assert stage.timeout_s == 30.0
    assert stage.extra_env == (("A", "1"), ("B", "2"))
    assert stage.tier == "integration"


def test_single_stage_plan_keeps_the_default_timeout(tmp_path: Path) -> None:
    (stage,) = cycle_run_mod.single_stage_plan(framework="pytest", target_repo=tmp_path).stages
    assert stage.timeout_s == 600.0
