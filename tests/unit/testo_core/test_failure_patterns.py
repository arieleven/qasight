"""Failure patterns (testo_core/services/failure_patterns.py)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from testo_core.services.failure_patterns import build_patterns
from tests.unit.testo_core.test_delivery_metrics import NOW, START, _run, cases  # noqa: F401


def _c(status: str, *, ms: int = 100, message: str = "", retried: bool = False) -> dict[str, Any]:
    return {"name": "", "status": status, "duration_ms": ms, "message": message, "retried": retried}


def _set(table: dict[str, Any], run_id: str, **tests: dict[str, Any]) -> None:
    table[run_id] = {k: {**v, "name": k} for k, v in tests.items()}


def test_concentration_errors_retries_timing_and_bugs(cases: dict[str, Any]) -> None:  # noqa: F811
    runs = [_run("old", 10)] + [_run(f"r{i}", 6.5 - i, red=i < 5) for i in range(6)]
    _set(cases, "old", t_a=_c("passed", retried=True), t_b=_c("passed"))
    for i in range(6):
        tests = {
            "t_a": _c("failed", message=f"Timeout after {1000 + i} ms") if i < 4 else _c("passed"),
            "t_b": _c("failed", message="AssertionError: total")
            if i == 0
            else _c("passed", ms=100 if i < 5 else 5000),
            "t_c": _c("failed", message=f"Timeout after {i} ms")
            if i == 4
            else _c("passed", retried=i == 5),
            "t_d": _c("passed"),
        }
        _set(cases, f"r{i}", **tests)

    def marked(cycle: str, messages: set[str]) -> dict[str, str]:
        return {m: "product_bug" if m.startswith("Assertion") else "environment" for m in messages}

    p = build_patterns(runs, start=START, end=NOW, causes=marked)
    assert p.failures == 6
    assert [(t.name, t.count) for t in p.top_tests] == [("t_a", 4)]
    assert p.top_share_pct == pytest.approx(66.7)
    (err,) = p.repeated_errors
    assert (err.count, err.tests) == (5, 2) and err.message.startswith("Timeout after")
    assert (p.retried, p.executed) == (1, 24)
    assert p.retry_pct == pytest.approx(4.2) and p.prev_retry_pct == pytest.approx(50.0)
    (slow,) = p.unstable_timing
    assert slow.name == "t_b" and slow.detail == "median 0.1s, slowest runs 5.0s"
    assert [(t.name, t.count, t.detail) for t in p.caught_bugs] == [("t_b", 1, "0 false alarms")]
    assert {t.name for t in p.only_noise} == {"t_a", "t_c"}
    assert sum(d.executed for d in p.days) == 24


def test_when_failures_happen(cases: dict[str, Any]) -> None:  # noqa: F811
    runs = []
    for i in range(12):
        run = _run(f"r{i}", 6 - i * 0.25, red=i % 2 == 0)
        runs.append(replace(run, environment="browser=firefox" if i % 2 == 0 else "browser=chrome"))
    p = build_patterns(runs, start=START, end=NOW)
    assert {s.label: (s.runs, s.red) for s in p.by_environment} == {
        "browser=firefox": (6, 6),
        "browser=chrome": (6, 0),
    }
    assert "Only runs on browser=firefox failed (6 of 6 red)." in p.when_findings
    assert sum(s.runs for s in p.by_hour) == 12 and sum(s.runs for s in p.by_weekday) == 12


def test_coverage_by_day(cases: dict[str, Any]) -> None:  # noqa: F811
    runs = [replace(_run("a", 2), coverage_pct=81.5), replace(_run("b", 1), coverage_pct=82.0)]
    p = build_patterns(runs, start=START, end=NOW, days=["1970-07-17", "1970-07-18", "1970-07-19"])
    assert [d.day for d in p.days] == ["1970-07-17", "1970-07-18", "1970-07-19"]
    assert [d.coverage_pct for d in p.days] == [None, 81.5, 82.0]
