"""``TESTO_RUN_QUEUE``: queued API runs in memory (default) or kept in the database."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from sqlmodel import create_engine

from qasight_api.cycle_execution_manager import CycleExecutionManager, CycleExecutionState
from qasight_api.run_queue import (
    DbRunStore,
    MemoryRunStore,
    RunRequest,
    run_queue_kind_from_env,
    run_store_from_env,
)
from tests.fixtures.engine import use_echo_adapter, write_minimal_config


@pytest.fixture
def store(tmp_path: Path) -> DbRunStore:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'queue.db'}", connect_args={"check_same_thread": False}
    )
    return DbRunStore(engine)


def _wait(state: CycleExecutionState, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not state.done:
        assert time.monotonic() < deadline, "execution did not finish"
        time.sleep(0.02)


def test_request_round_trips_through_json() -> None:
    request = RunRequest(
        kind="adhoc",
        cycle="adhoc",
        config_path="/cfg/qasight.yaml",
        reporter_override=("slack",),
        framework="pytest",
        target_repo="/repo",
        args=("-q", "-k", "smoke"),
        timeout_s=30.0,
        extra_env={"A": "1"},
    )
    assert RunRequest.from_json(request.to_json()) == request
    # Keys from a newer version are ignored rather than failing the restart.
    assert RunRequest.from_json({**request.to_json(), "added_later": 1}) == request
    chosen = RunRequest(kind="cycle", cycle="c", tests=("test_a", "test_b"), rerun_failed="r1")
    assert RunRequest.from_json(chosen.to_json()) == chosen


def test_db_store_keeps_rows_in_order_and_claims_each_once(store: DbRunStore) -> None:
    store.add("b", RunRequest(kind="cycle", cycle="two"), created_at=2.0)
    store.add("a", RunRequest(kind="cycle", cycle="one"), created_at=1.0)

    assert [(p.execution_id, p.request.cycle, p.created_at) for p in store.pending()] == [
        ("a", "one", 1.0),
        ("b", "two", 2.0),
    ]
    assert store.claim("a") is True
    assert store.claim("a") is False  # a second process that also re-queued it skips it
    assert [p.execution_id for p in store.pending()] == ["b"]


def test_memory_store_keeps_nothing() -> None:
    store = MemoryRunStore()
    store.add("a", RunRequest(kind="cycle", cycle="one"), created_at=1.0)
    assert store.pending() == ()
    assert store.claim("a") is True


def test_queue_kind_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESTO_RUN_QUEUE", raising=False)
    assert run_queue_kind_from_env() == "memory"
    assert isinstance(run_store_from_env(), MemoryRunStore)
    monkeypatch.setenv("TESTO_RUN_QUEUE", " DB ")
    assert run_queue_kind_from_env() == "db"
    monkeypatch.setenv("TESTO_RUN_QUEUE", "redis")
    assert run_queue_kind_from_env() == "memory"


def test_a_waiting_run_survives_a_restart_with_the_db_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: DbRunStore
) -> None:
    adapter = use_echo_adapter(monkeypatch)
    config = write_minimal_config(tmp_path)
    before = CycleExecutionManager(max_concurrent=1, run_store=store)

    # Hold the only worker so the next run has to wait.
    release = threading.Event()

    def blocked():  # noqa: ANN202
        assert release.wait(10)
        raise RuntimeError("stop")

    busy = before._start(RunRequest(kind="cycle", cycle="busy"), blocked)
    deadline = time.monotonic() + 5
    # The worker sets "running" just before it claims (deletes) its row, so wait for both.
    while busy.status != "running" or any(
        p.execution_id == busy.execution_id for p in store.pending()
    ):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    waiting = before.create_execution(cycle="smoke", config_path=config, report_db=False)
    assert waiting.status == "queued"
    assert [p.execution_id for p in store.pending()] == [waiting.execution_id]

    # The "restarted" server finds the row and runs it under the same id.
    after = CycleExecutionManager(max_concurrent=1, run_store=store)
    assert after.recover_queued() == 1
    recovered = after.get(waiting.execution_id)
    assert recovered is not None
    _wait(recovered)
    assert recovered.status == "completed", recovered.error
    assert len(adapter.calls) == 1
    assert store.pending() == []

    # The old process's copy loses the claim and never runs it a second time.
    release.set()
    _wait(busy)
    deadline = time.monotonic() + 5
    while before.get(waiting.execution_id) is not None:
        assert time.monotonic() < deadline, "the old process kept the claimed run"
        time.sleep(0.02)
    assert len(adapter.calls) == 1


def test_a_run_store_error_on_add_frees_the_cycle() -> None:
    class Broken(MemoryRunStore):
        def add(self, execution_id: str, request: RunRequest, created_at: float) -> None:
            raise OSError("database is locked")

    manager = CycleExecutionManager(run_store=Broken())
    with pytest.raises(OSError):
        manager._start(RunRequest(kind="cycle", cycle="c"), lambda: (None, None))  # type: ignore[arg-type,return-value]
    with pytest.raises(OSError):  # the cycle is not left marked busy (no 409)
        manager._start(RunRequest(kind="cycle", cycle="c"), lambda: (None, None))  # type: ignore[arg-type,return-value]
