"""The read-side cache: reused while run history is unchanged, rebuilt when it changes."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest

from qasight.history import memo
from qasight.repository.db import get_repository
from qasight.repository.models import RunStatus

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.fixture(autouse=True)
def cache_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_READ_CACHE_S", "60")
    memo.clear()


def _add_run() -> None:
    get_repository().create_run(
        status=RunStatus.COMPLETED, metadata={"cycle": "smoke"}, start_time=datetime.now(UTC)
    )


def test_reuses_the_result_until_a_run_is_added() -> None:
    calls: list[int] = []

    def compute() -> int:
        calls.append(1)
        return len(calls)

    assert memo.cached("view", (), compute) == 1
    assert memo.cached("view", (), compute) == 1
    _add_run()
    assert memo.cached("view", (), compute) == 2


def test_arguments_are_part_of_the_key() -> None:
    assert memo.cached("view", ("a",), lambda: "a") == "a"
    assert memo.cached("view", ("b",), lambda: "b") == "b"


def test_zero_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_READ_CACHE_S", "0")
    calls: list[int] = []
    memo.cached("view", (), lambda: calls.append(1))
    memo.cached("view", (), lambda: calls.append(1))
    assert len(calls) == 2


def test_concurrent_misses_compute_once() -> None:
    calls: list[int] = []

    def slow() -> str:
        calls.append(1)
        time.sleep(0.2)
        return "done"

    results: list[str] = []
    threads = [
        threading.Thread(target=lambda: results.append(memo.cached("slow", (), slow)))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["done"] * 8
    assert len(calls) == 1


def test_an_error_is_not_cached() -> None:
    def boom() -> None:
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        memo.cached("view", (), boom)
    assert memo.cached("view", (), lambda: "ok") == "ok"
