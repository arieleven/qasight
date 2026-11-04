"""Engine exit-code taxonomy — the single source of truth for process outcomes.

The CLI must propagate these unchanged so existing CI consumers continue to
react to specific codes.  The legacy headless engine re-exports this class.
"""

from __future__ import annotations

from enum import IntEnum


class EngineExitCode(IntEnum):
    """High-level outcome categorisation for the ``qasight`` process."""

    SUCCESS = 0
    DOMAIN_FAILURE = 1
    INVALID_INPUT = 2
    INFRA_FAILURE = 3
    INTERNAL_ERROR = 4
    # Stopped by a signal: the stages were stopped and the run recorded (as failed).
    INTERRUPTED = 130  # SIGINT (Ctrl-C)
    TERMINATED = 143  # SIGTERM (a CI job's cancel button, `kill`)


def classify_exit_code(
    returncodes: list[int],
    *,
    infra_error: Exception | None,
    internal_failure: bool = False,
) -> EngineExitCode:
    """Bucket a list of stage returncodes into an :class:`EngineExitCode`.

    ``internal_failure`` distinguishes an orchestrator-caught engine exception
    (exit **4**) from a framework subprocess that happened to return 4 (exit
    **1**) — see the classification table in
    ``docs/CLI Commands/Troubleshooting and Error Codes.md``.
    """
    if infra_error is not None:
        return EngineExitCode.INFRA_FAILURE
    if internal_failure:
        return EngineExitCode.INTERNAL_ERROR
    if not returncodes:
        return EngineExitCode.INTERNAL_ERROR
    if any(int(rc) in (124, 127) for rc in returncodes):
        return EngineExitCode.INFRA_FAILURE
    if any(int(rc) != 0 for rc in returncodes):
        return EngineExitCode.DOMAIN_FAILURE
    return EngineExitCode.SUCCESS
