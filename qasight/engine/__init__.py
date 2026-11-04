"""Subprocess orchestrator: pure execution engine with no terminal dependencies.

``qasight.engine`` consumes :class:`qasight.config.schema.Plan` objects
and produces :class:`PlanResult` (via the :func:`run_plan` entry-point).  It
must never import from ``qasight.cli`` or ``qasight.reporting``.
"""

from qasight.engine.events import (
    EngineEvent,
    PlanFinished,
    PlanStarted,
    StageFinished,
    StageOutputChunk,
    StageStarted,
)
from qasight.engine.exit_codes import EngineExitCode, classify_exit_code
from qasight.engine.result import PlanResult, StageResult

__all__ = [
    "EngineEvent",
    "EngineExitCode",
    "PlanFinished",
    "PlanResult",
    "PlanStarted",
    "StageFinished",
    "StageOutputChunk",
    "StageResult",
    "StageStarted",
    "classify_exit_code",
]
