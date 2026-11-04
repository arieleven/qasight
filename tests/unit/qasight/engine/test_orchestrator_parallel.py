"""``max_parallel_stages``: up to N stages at once, events and results stay consistent.

The executor is stubbed with stages that block on events, so the tests control
exactly which stages overlap and in which order they finish.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from qasight.config.schema import Plan, Stage
from qasight.engine import orchestrator
from qasight.engine.events import EngineEvent, StageFinished, StageStarted
from qasight.engine.result import StageResult
from qasight.paths import plan_artifacts_dir

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


class _RecordingRenderer:
    wants_streaming = True  # parallel runs must still buffer output

    def __init__(self) -> None:
        self.events: list[EngineEvent] = []
        self.threads: set[str] = set()

    def handle(self, event: EngineEvent) -> None:
        self.threads.add(threading.current_thread().name)
        self.events.append(event)


def _plan(*names: str, max_parallel: int) -> Plan:
    return Plan(
        name="parallel-plan",
        description=None,
        stages=tuple(Stage(name=n, framework="pytest", target_repo=Path(".")) for n in names),
        max_parallel_stages=max_parallel,
    )


class _ScriptedStages:
    """Each stage waits for its ``release`` event, then returns its scripted returncode."""

    def __init__(self, returncodes: dict[str, int], *, duration_s: float = 0.0) -> None:
        self.returncodes = returncodes
        self.release = {name: threading.Event() for name in returncodes}
        self.lock = threading.Lock()
        self.running: set[str] = set()
        self.peak = 0
        self.started: list[str] = []
        self.chunk_callbacks: list[object] = []
        self.duration_s = duration_s

    def __call__(
        self, stage: Stage, *, plan_name: str, artifacts_root: Path, on_chunk=None, **_: object
    ) -> StageResult:
        with self.lock:
            self.started.append(stage.name)
            self.running.add(stage.name)
            self.peak = max(self.peak, len(self.running))
            self.chunk_callbacks.append(on_chunk)
        assert self.release[stage.name].wait(10), f"{stage.name} never released"
        with self.lock:
            self.running.discard(stage.name)
        now = time.time()
        return StageResult(
            stage_name=stage.name,
            framework=stage.framework,
            returncode=self.returncodes[stage.name],
            started_at=now,
            finished_at=now,
            duration_s=self.duration_s,
            log_path=None,
            artifacts_dir=artifacts_root / plan_name / stage.name,
            command=("pytest",),
            output_tail="",
        )

    def wait_started(self, *names: str) -> None:
        deadline = time.monotonic() + 10
        while not set(names) <= set(self.started):
            assert time.monotonic() < deadline, f"started only {self.started}"
            time.sleep(0.01)


def _run_in_background(plan: Plan, tmp_path: Path, renderer: _RecordingRenderer, **kwargs):
    box: dict[str, object] = {}

    def target() -> None:
        box["result"] = orchestrator.run_plan(
            plan, renderer=renderer, artifacts_root=tmp_path, persist=False, **kwargs
        )

    thread = threading.Thread(target=target, name="caller")
    thread.start()
    return thread, box


def _events(tmp_path: Path) -> list[dict[str, object]]:
    path = plan_artifacts_dir(tmp_path, "parallel-plan") / "events.ndjson"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_runs_at_most_n_stages_at_once_and_keeps_config_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stages = _ScriptedStages({"a": 0, "b": 1, "c": 0})
    monkeypatch.setattr(orchestrator, "run_stage", stages)
    renderer = _RecordingRenderer()
    thread, box = _run_in_background(_plan("a", "b", "c", max_parallel=2), tmp_path, renderer)

    stages.wait_started("a", "b")
    time.sleep(0.1)
    assert stages.started == ["a", "b"]  # c waits for a free slot
    stages.release["b"].set()  # b finishes first...
    stages.wait_started("c")  # ...and its slot goes to c
    stages.release["a"].set()
    stages.release["c"].set()
    thread.join(10)

    result = box["result"]
    assert stages.peak == 2
    assert [s.stage_name for s in result.stages] == ["a", "b", "c"]  # config order
    assert result.aggregate_returncode == 1
    assert int(result.exit_code) == 1
    finished = [e["stage"] for e in _events(tmp_path) if e["event"] == "stage_finished"]
    assert finished[0] == "b"  # NDJSON records completion order
    assert _events(tmp_path)[-1]["event"] == "plan_finished"
    # Only the calling thread touches the renderer, and output is not streamed live.
    assert renderer.threads == {"caller"}
    assert stages.chunk_callbacks == [None, None, None]
    started = [e for e in renderer.events if isinstance(e, StageStarted)]
    assert [e.stage_index for e in started] == [1, 2, 3]
    assert len([e for e in renderer.events if isinstance(e, StageFinished)]) == 3


def test_fail_fast_lets_running_stages_finish_and_starts_no_new_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stages = _ScriptedStages({"a": 0, "b": 1, "c": 0})
    monkeypatch.setattr(orchestrator, "run_stage", stages)
    thread, box = _run_in_background(
        _plan("a", "b", "c", max_parallel=2), tmp_path, _RecordingRenderer(), fail_fast=True
    )

    stages.wait_started("a", "b")
    stages.release["b"].set()  # fails while a is still running
    time.sleep(0.1)
    stages.release["a"].set()
    thread.join(10)

    assert stages.started == ["a", "b"]
    result = box["result"]
    assert [s.stage_name for s in result.stages] == ["a", "b"]
    events = _events(tmp_path)
    aborted = [e for e in events if e["event"] == "plan_aborted"]
    assert aborted == [
        {
            "event": "plan_aborted",
            "plan": "parallel-plan",
            "reason": "fail_fast",
            "completed_stages": 2,
        }
    ]
    # a's result is recorded before the abort, and plan_finished stays last.
    names = [e["event"] for e in events]
    assert names.index("plan_aborted") > max(
        i for i, n in enumerate(names) if n == "stage_finished"
    )
    assert names[-1] == "plan_finished"


def test_engine_crash_in_one_stage_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def flaky_run_stage(stage: Stage, *, plan_name: str, artifacts_root: Path, **_: object):
        if stage.name == "boom":
            raise RuntimeError("adapter bug")
        now = time.time()
        return StageResult(
            stage_name=stage.name,
            framework=stage.framework,
            returncode=0,
            started_at=now,
            finished_at=now,
            duration_s=0.0,
            log_path=None,
            artifacts_dir=artifacts_root,
            command=(),
            output_tail="",
        )

    monkeypatch.setattr(orchestrator, "run_stage", flaky_run_stage)
    result = orchestrator.run_plan(
        _plan("ok", "boom", "ok-too", max_parallel=3),
        renderer=_RecordingRenderer(),
        artifacts_root=tmp_path,
        persist=False,
    )

    assert [s.stage_name for s in result.stages] == ["ok", "boom", "ok-too"]
    assert result.stages[1].internal_failure
    assert int(result.exit_code) == 4


def test_parallel_duration_counts_the_longest_stage_not_the_sum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stages = _ScriptedStages({"a": 0, "b": 0}, duration_s=30.0)
    for event in stages.release.values():
        event.set()
    monkeypatch.setattr(orchestrator, "run_stage", stages)
    result = orchestrator.run_plan(
        _plan("a", "b", max_parallel=2),
        renderer=_RecordingRenderer(),
        artifacts_root=tmp_path,
        persist=False,
    )
    assert result.duration_s == pytest.approx(30.0, abs=1.0)


def test_default_is_sequential_on_the_calling_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    threads: list[str] = []

    def record(stage: Stage, *, plan_name: str, artifacts_root: Path, **_: object):
        threads.append(threading.current_thread().name)
        now = time.time()
        return StageResult(
            stage_name=stage.name,
            framework=stage.framework,
            returncode=0,
            started_at=now,
            finished_at=now,
            duration_s=0.0,
            log_path=None,
            artifacts_dir=artifacts_root,
            command=(),
            output_tail="",
        )

    monkeypatch.setattr(orchestrator, "run_stage", record)
    orchestrator.run_plan(
        _plan("a", "b", max_parallel=1),
        renderer=_RecordingRenderer(),
        artifacts_root=tmp_path,
        persist=False,
    )
    assert threads == [threading.current_thread().name] * 2
