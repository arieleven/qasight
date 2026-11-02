"""Failure signal (testo_core/services/failure_signal.py): real bugs vs noise, cost of flakiness."""

from __future__ import annotations

from typing import Any

import pytest

from testo_core.services import flaky_service
from testo_core.services.failure_signal import build_signal, run_cause
from tests.unit.testo_core.test_delivery_metrics import NOW, START, _run, cases  # noqa: F401


def _set(table: dict[str, Any], run_id: str, **tests: tuple[str, str] | str) -> None:
    table[run_id] = {}
    for key, value in tests.items():
        status, message = value if isinstance(value, tuple) else (value, "")
        table[run_id][key] = {
            "name": key,
            "status": status,
            "message": message,
            "duration_ms": 2000,
            "retried": False,
        }


def test_run_cause_order() -> None:
    fail = {"status": "failed", "message": "boom"}
    assert run_cause({}, set(), {}) == "no_tests"
    assert run_cause({"a": fail}, {"a"}, {}) == "flaky"
    assert run_cause({"a": fail, "b": {**fail, "message": "x"}}, {"a"}, {}) == "unmarked"
    assert run_cause({"a": fail}, set(), {"boom": "environment"}) == "environment"
    assert (
        run_cause(
            {"a": fail, "b": {**fail, "message": "bug"}},
            set(),
            {"boom": "environment", "bug": "product_bug"},
        )
        == "product_bug"
    )


def test_red_runs_by_cause_and_flaky_cost(cases: dict[str, Any]) -> None:  # noqa: F811
    runs = [
        # The week before: test_flip flips on main, making it flaky.
        _run("o1", 12, red=True, ms=120_000),
        _run("o2", 11, ms=120_000),
        _run("o3", 10, red=True, ms=120_000),
        _run("o4", 9, ms=120_000),
        # This week.
        _run("n1", 6, red=True, ms=60_000),  # only test_flip failed: flaky
        _run("n2", 5, red=True, ms=60_000),  # a marked product bug
        _run("n3", 4, red=True, ms=60_000),  # an unmarked failure
        _run("n4", 3, red=True, ms=60_000),  # red with no failing test (a crash)
        _run("n5", 2, red=True, ms=60_000, branch="feature", base="main"),  # flaky on a PR
        _run("n6", 1, ms=60_000),
    ]
    for rid in ("o1", "o3"):
        _set(cases, rid, test_flip="failed", test_ok="passed")
    for rid in ("o2", "o4", "n6"):
        _set(cases, rid, test_flip="passed", test_ok="passed")
    _set(cases, "n1", test_flip="failed", test_ok="passed")
    _set(cases, "n2", test_flip="passed", test_ok=("failed", "AssertionError: total 3"))
    _set(cases, "n3", test_flip="passed", test_ok="passed", test_new=("failed", "KeyError: x"))
    _set(cases, "n4", test_flip="passed", test_ok="passed")
    _set(cases, "n5", test_flip="failed", test_ok="passed")
    cases["n6"]["test_flip"]["retried"] = True  # failed once, passed on retry

    def marked(cycle: str, messages: set[str]) -> dict[str, str]:
        assert cycle == "api"
        return {m: "product_bug" for m in messages if m.startswith("AssertionError")}

    s = build_signal(runs, start=START, end=NOW, default_branch="main", causes=marked)
    assert s.red_runs == 5 and s.prev_red_runs == 2
    assert {c.cause: c.runs for c in s.by_cause} == {
        "product_bug": 1,
        "unmarked": 1,
        "flaky": 2,
        "no_tests": 1,
    }
    assert (s.real_pct, s.noise_pct, s.unmarked_pct) == (20.0, 60.0, 20.0)
    assert s.prev_noise_pct == 100.0
    assert s.flaky_red_runs == 2 and s.flaky_branches == 1
    # Two one-minute red runs plus a two-second retry.
    assert s.flaky_minutes == pytest.approx(2.0)
    (top,) = s.costly
    assert (top.name, top.red_runs, top.retries, top.branches) == (
        "test_flip",
        2,
        1,
        ["feature", "main"],
    )
    assert s.prev_flaky_minutes == pytest.approx(4.0)


def test_flaky_report_sorts_by_cost(cases: dict[str, Any]) -> None:  # noqa: F811
    runs = [_run(f"r{i}", 9 - i, red=i % 2 == 0, ms=60_000) for i in range(8)]
    for i in range(8):
        _set(cases, f"r{i}", test_a="failed" if i % 2 == 0 else "passed", test_b="passed")
    _set(cases, "r7", test_a="passed", test_b="passed")
    cases["r7"]["test_b"]["retried"] = True
    cases["r5"]["test_b"]["status"] = "failed"
    report = flaky_service._report("api", list(reversed(runs)), 10)
    assert [t.name for t in report.flaky][0] == "test_a"
    assert report.flaky[0].red_runs == 4 and report.flaky[0].cost_minutes == pytest.approx(4.0)
