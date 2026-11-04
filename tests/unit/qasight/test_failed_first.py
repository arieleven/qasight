"""Rank and order tests by their recent failures (qasight/services/failed_first.py)."""

from __future__ import annotations

from qasight.services.failed_first import (
    FAILED_LAST,
    FAILED_RECENTLY,
    FLAKY,
    REST,
    order,
    rank_index,
    rank_of,
)
from qasight.services.test_split import nodeid_names


def test_rank_of() -> None:
    assert rank_of({"last_status": "broken"}) == FAILED_LAST
    assert rank_of({"last_status": "passed", "flaky": True, "failed": 3}) == FLAKY
    assert rank_of({"last_status": "passed", "failed": 1}) == FAILED_RECENTLY
    assert rank_of({"last_status": "passed", "failed": 0}) == REST
    assert rank_of({}) == REST


def test_rank_index_keeps_only_tests_worth_moving() -> None:
    ranks = rank_index(
        [
            {"key": "k1", "full_name": "tests.test_a.test_x", "last_status": "failed"},
            {"key": "k2", "full_name": "tests.test_a.test_y", "last_status": "passed"},
        ]
    )
    assert ranks == {"tests.test_a.test_x": FAILED_LAST, "k1": FAILED_LAST}


def test_order_is_stable_inside_a_rank() -> None:
    tests = [
        "tests/test_a.py::test_ok",
        "tests/test_a.py::test_flaky",
        "tests/test_a.py::test_red",
        "tests/test_a.py::test_ok2",
        "tests/test_a.py::test_once",
    ]
    ranks = {
        "tests.test_a.test_red": FAILED_LAST,
        "tests.test_a#test_flaky": FLAKY,
        "tests.test_a.test_once": FAILED_RECENTLY,
    }
    positions, counts = order(tests, ranks, names_for=nodeid_names)
    assert [tests[i] for i in positions] == [
        "tests/test_a.py::test_red",
        "tests/test_a.py::test_flaky",
        "tests/test_a.py::test_once",
        "tests/test_a.py::test_ok",
        "tests/test_a.py::test_ok2",
    ]
    assert counts == {FAILED_LAST: 1, FLAKY: 1, FAILED_RECENTLY: 1}


def test_no_history_keeps_the_order() -> None:
    positions, counts = order(["a", "b", "c"], {})
    assert positions == [0, 1, 2] and counts == {}
