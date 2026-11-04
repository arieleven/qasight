"""Formal ``EngineExitCode`` 0–4 contract (QA Strategies section A).

This is the frozen public contract CI consumers depend on: the enum values,
the classifier precedence, and the documented edge cases (EC-04, EC-07).
``test_exit_code_consolidation.py`` covers canonical-import identity; this
module covers semantics.  Changing any assertion here is a breaking change
that must be reflected in ``docs/CLI Commands/Troubleshooting and Error
Codes.md`` and announced to CI integrators.
"""

from __future__ import annotations

import pytest

from qasight.engine.exit_codes import EngineExitCode, classify_exit_code

pytestmark = [pytest.mark.contract, pytest.mark.tier_fast]


def test_exit_code_values_are_frozen() -> None:
    assert {member.name: member.value for member in EngineExitCode} == {
        "SUCCESS": 0,
        "DOMAIN_FAILURE": 1,
        "INVALID_INPUT": 2,
        "INFRA_FAILURE": 3,
        "INTERNAL_ERROR": 4,
        # Stopped by a signal (shell convention 128 + signal number); never returned by
        # classify_exit_code, only by run_plan when SIGINT / SIGTERM stopped the run.
        "INTERRUPTED": 130,
        "TERMINATED": 143,
    }


def test_exit_codes_are_ints() -> None:
    # `raise typer.Exit(code=int(exit_code))` and NDJSON payloads rely on
    # IntEnum semantics.
    assert all(isinstance(member, int) for member in EngineExitCode)
    assert int(EngineExitCode.INFRA_FAILURE) == 3


class TestClassifierPrecedence:
    """infra_error > internal_failure > empty list > 124/127 > non-zero > 0."""

    def test_infra_error_beats_internal_failure(self) -> None:
        result = classify_exit_code([0], infra_error=OSError("x"), internal_failure=True)
        assert result is EngineExitCode.INFRA_FAILURE

    def test_internal_failure_beats_stage_returncodes(self) -> None:
        result = classify_exit_code([127], infra_error=None, internal_failure=True)
        assert result is EngineExitCode.INTERNAL_ERROR

    def test_empty_returncodes_is_internal_error(self) -> None:
        assert classify_exit_code([], infra_error=None) is EngineExitCode.INTERNAL_ERROR

    def test_infra_returncodes_beat_domain_failures(self) -> None:
        assert classify_exit_code([1, 124], infra_error=None) is EngineExitCode.INFRA_FAILURE
        assert classify_exit_code([1, 127], infra_error=None) is EngineExitCode.INFRA_FAILURE


@pytest.mark.parametrize(
    ("returncodes", "expected"),
    [
        pytest.param([0], EngineExitCode.SUCCESS, id="EC-00-pass"),
        pytest.param([2], EngineExitCode.DOMAIN_FAILURE, id="EC-01-framework-failure"),
        pytest.param([127], EngineExitCode.INFRA_FAILURE, id="EC-03a-missing-binary"),
        pytest.param([124], EngineExitCode.INFRA_FAILURE, id="EC-03b-timeout"),
        pytest.param([], EngineExitCode.INTERNAL_ERROR, id="EC-04-empty-returncodes"),
        # EC-07: raw framework rc=4 is a *domain* failure — INTERNAL_ERROR is
        # only reachable through the internal_failure flag or an empty list.
        pytest.param([4], EngineExitCode.DOMAIN_FAILURE, id="EC-07-raw-4-is-domain"),
        # Documented misclassification lock (Troubleshooting doc): signal
        # deaths like SIGKILL (137) are still classified as domain failures.
        pytest.param([137], EngineExitCode.DOMAIN_FAILURE, id="sigkill-137-locked-as-domain"),
    ],
)
def test_contract_rows(returncodes: list[int], expected: EngineExitCode) -> None:
    assert classify_exit_code(returncodes, infra_error=None) is expected


def test_internal_failure_flag_contract() -> None:
    # EC-04: orchestrator-caught engine exceptions flow through the flag, so
    # the synthetic rc=4 stage result classifies as 4 — not as domain failure.
    assert (
        classify_exit_code([4], infra_error=None, internal_failure=True)
        is EngineExitCode.INTERNAL_ERROR
    )
