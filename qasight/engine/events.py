"""Discrete events emitted by the orchestrator to renderers.

The orchestrator does not know which renderer is attached.  It simply calls
:meth:`Renderer.handle` (or the dedicated ``on_*`` methods); each renderer
chooses what to do with the event (Rich panels, NDJSON line, no-op).
"""

from __future__ import annotations

from dataclasses import dataclass

from qasight.config.schema import Plan, Stage
from qasight.engine.result import PlanResult, StageResult


@dataclass(frozen=True)
class PlanStarted:
    plan: Plan


@dataclass(frozen=True)
class StageStarted:
    stage: Stage
    stage_index: int
    stage_count: int


@dataclass(frozen=True)
class StageOutputChunk:
    """Streamed only when the renderer requested live output (``--stream``)."""

    stage_name: str
    chunk: bytes


@dataclass(frozen=True)
class StageFinished:
    result: StageResult


@dataclass(frozen=True)
class PlanFinished:
    result: PlanResult


EngineEvent = PlanStarted | StageStarted | StageOutputChunk | StageFinished | PlanFinished
