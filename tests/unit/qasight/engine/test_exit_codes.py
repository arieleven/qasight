"""Unit tests for ``classify_exit_code`` (QA Strategies EC rows).

Locks the classification table in
``docs/CLI Commands/Troubleshooting and Error Codes.md``, including the known
misclassifications documented there (e.g. SIGKILL rc=137 → exit 1).
"""

from __future__ import annotations

import pytest

from qasight.engine.exit_codes import EngineExitCode, classify_exit_code

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.mark.parametrize(
    ("returncodes", "expected"),
    [
        ([0], EngineExitCode.SUCCESS),
        ([0, 0, 0], EngineExitCode.SUCCESS),
        ([1], EngineExitCode.DOMAIN_FAILURE),
        ([0, 1], EngineExitCode.DOMAIN_FAILURE),
        ([2], EngineExitCode.DOMAIN_FAILURE),
        # EC-07: a framework subprocess returning the raw value 4 is a domain
        # failure — INTERNAL_ERROR is reserved for the internal_failure flag.
        ([4], EngineExitCode.DOMAIN_FAILURE),
        # Documented misclassification lock: SIGKILL'd frameworks (137) count
        # as domain failures until the classifier learns about signals.
        ([137], EngineExitCode.DOMAIN_FAILURE),
        ([-15], EngineExitCode.DOMAIN_FAILURE),
        # EC-03a/EC-03b: 127 (missing binary) and 124 (timeout) are infra.
        ([127], EngineExitCode.INFRA_FAILURE),
        ([124], EngineExitCode.INFRA_FAILURE),
        ([0, 127], EngineExitCode.INFRA_FAILURE),
        # Infra beats domain when both appear.
        ([1, 124], EngineExitCode.INFRA_FAILURE),
        # EC-04: empty returncode list means the engine never ran a stage.
        ([], EngineExitCode.INTERNAL_ERROR),
    ],
    ids=repr,
)
def test_classification_table(returncodes: list[int], expected: EngineExitCode) -> None:
    assert classify_exit_code(returncodes, infra_error=None) is expected


def test_infra_error_wins_over_everything() -> None:
    err = RuntimeError("docker daemon unreachable")
    assert classify_exit_code([0], infra_error=err) is EngineExitCode.INFRA_FAILURE
    assert classify_exit_code([], infra_error=err) is EngineExitCode.INFRA_FAILURE
    assert (
        classify_exit_code([1], infra_error=err, internal_failure=True)
        is EngineExitCode.INFRA_FAILURE
    )


def test_internal_failure_flag_forces_internal_error() -> None:
    # EC-04: orchestrator-caught engine exception, not a framework exit code.
    assert (
        classify_exit_code([4], infra_error=None, internal_failure=True)
        is EngineExitCode.INTERNAL_ERROR
    )
    assert (
        classify_exit_code([0, 0], infra_error=None, internal_failure=True)
        is EngineExitCode.INTERNAL_ERROR
    )


def test_returncodes_are_coerced_to_int() -> None:
    # The orchestrator may hand over bool-ish/str-ish codes from adapters.
    assert classify_exit_code(["127"], infra_error=None) is EngineExitCode.INFRA_FAILURE  # type: ignore[list-item]
    assert classify_exit_code(["0"], infra_error=None) is EngineExitCode.SUCCESS  # type: ignore[list-item]
