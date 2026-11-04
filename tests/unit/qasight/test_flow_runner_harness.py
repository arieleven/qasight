from __future__ import annotations

from tests.conftest import CleanupLedger
from tests.e2e.flows.flow_runner import run_flow_scenario
from tests.e2e.flows.flow_scenario import FlowContext, FlowScenario


class _Provisioner:
    def __init__(self, *, fail_provision: bool = False) -> None:
        self.fail_provision = fail_provision
        self.cleaned = False

    def provision(self, ctx: FlowContext) -> None:
        ctx.resources["x"] = "1"
        if self.fail_provision:
            raise RuntimeError("provision failed")

    def cleanup(self, ctx: FlowContext) -> None:
        self.cleaned = True
        ctx.resources.pop("x", None)


class _Executor:
    def execute(self, ctx: FlowContext) -> None:
        ctx.metadata["executed"] = True

    def poll(self, ctx: FlowContext) -> None:
        ctx.metadata["polled"] = True


class _Verifier:
    def verify(self, ctx: FlowContext) -> None:
        assert ctx.metadata["executed"] is True
        assert ctx.metadata["polled"] is True


def test_flow_runner_success_records_cleanup() -> None:
    provisioner = _Provisioner()
    scenario = FlowScenario(
        name="s1", provisioner=provisioner, executor=_Executor(), verifiers=[_Verifier()]
    )
    ctx = FlowContext(run_id="rid", provider="local")
    ledger = CleanupLedger(run_id="rid")

    result = run_flow_scenario(scenario, ctx, ledger)

    assert result.status == "success"
    assert provisioner.cleaned is True
    assert ledger.records and ledger.records[-1].status == "cleaned"


def test_flow_runner_failure_still_cleans_up() -> None:
    provisioner = _Provisioner(fail_provision=True)
    scenario = FlowScenario(
        name="s2", provisioner=provisioner, executor=_Executor(), verifiers=[_Verifier()]
    )
    ctx = FlowContext(run_id="rid", provider="local")
    ledger = CleanupLedger(run_id="rid")

    result = run_flow_scenario(scenario, ctx, ledger)

    assert result.status == "failed"
    assert provisioner.cleaned is True
    assert ledger.records and ledger.records[-1].status == "cleaned"
