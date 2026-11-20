"""``test_result`` rows: one test's outcome in one run, read by the analytics instead of files."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from qasight.repository import migrations
from qasight.repository.test_results import TestResultStore

pytestmark = pytest.mark.tier_fast


def _store(tmp_path: Path) -> TestResultStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    migrations.upgrade_to_head(engine)
    return TestResultStore(engine)


def _case(status: str, **extra: object) -> dict[str, object]:
    case = {
        "name": "test_a",
        "full_name": "tests.test_m.test_a",
        "group": "tests.test_m",
        "status": status,
        "duration_ms": 12,
        "message": "",
        "stage": "unit",
        "framework": "pytest",
        "crash": "",
        "retried": False,
        "labels": [],
        "links": [],
    }
    case.update(extra)
    return case


def test_rows_round_trip_as_the_case_map(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cases = {
        "a": _case("passed"),
        "b": _case("failed", name="test_b", message="boom", crash="stack_overflow"),
        "c": _case("passed", name="test_c", retried=True),
        "d": _case(
            "passed",
            name="test_d",
            labels=[{"name": "feature", "value": "Checkout"}],
            links=[{"name": "PROJ-1", "url": "https://jira.example/PROJ-1", "type": "issue"}],
        ),
    }

    assert store.replace("run-1", cases) == 4

    assert store.cases("run-1") == cases
    assert store.has_rows("run-1")
    assert store.cases("run-2") == {}
    assert not store.has_rows("run-2")


def test_replace_drops_the_runs_earlier_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace("run-1", {"a": _case("passed"), "b": _case("passed", name="test_b")})
    store.replace("run-1", {"a": _case("failed")})

    assert list(store.cases("run-1")) == ["a"]
    assert store.cases("run-1")["a"]["status"] == "failed"


def test_delete_runs_removes_only_those_runs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace("run-1", {"a": _case("passed")})
    store.replace("run-2", {"a": _case("passed")})
    store.replace("run-3", {"a": _case("passed")})

    assert store.delete_runs(["run-1", "run-3", "missing"]) == 2
    assert store.delete_runs([]) == 0

    assert not store.has_rows("run-1")
    assert store.has_rows("run-2")
    assert not store.has_rows("run-3")


def test_long_values_are_cut_to_the_column_widths(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace("r", {"k" * 300: _case("passed", name="n" * 500, message="m" * 1000)})

    (key, case), *_ = store.cases("r").items()
    assert len(key) == 200
    assert len(str(case["name"])) == 120
    assert len(str(case["message"])) == 300


def test_a_non_integer_duration_is_stored_as_unknown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace(
        "r", {"a": _case("passed", duration_ms=None), "b": _case("passed", duration_ms="x")}
    )

    assert store.cases("r")["a"]["duration_ms"] is None
    assert store.cases("r")["b"]["duration_ms"] is None


def test_concurrent_reads_return_the_same_rows(tmp_path: Path) -> None:
    # SQLite reads of a run's rows are taken one at a time per process (the sqlite3
    # module's per-row GIL hand-off made concurrent reads ~90x slower); they must still
    # all finish and agree.
    from concurrent.futures import ThreadPoolExecutor

    store = _store(tmp_path)
    cases = {
        f"t{i}": _case("failed" if i % 7 == 0 else "passed", labels=[{"name": "tag", "value": "x"}])
        for i in range(300)
    }
    store.replace("run-1", cases)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: store.cases("run-1"), range(16)))
    assert all(r == results[0] for r in results)
    assert results[0]["t7"]["status"] == "failed"
    assert results[0]["t1"]["labels"] == [{"name": "tag", "value": "x"}]
    assert len(results[0]) == 300


def test_cached_runs_follow_replace_and_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace("run-1", {"a": _case("passed")})
    assert store.cases("run-1")["a"]["status"] == "passed"  # now cached
    store.replace("run-1", {"a": _case("failed")})
    assert store.cases("run-1")["a"]["status"] == "failed"
    store.delete_runs(["run-1"])
    assert store.cases("run-1") == {}


def test_callers_can_change_the_map_they_get(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace("run-1", {"a": _case("passed"), "b": _case("passed")})
    store.cases("run-1").pop("a")
    assert set(store.cases("run-1")) == {"a", "b"}


def test_cache_budget_zero_turns_it_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from qasight.repository import test_results

    monkeypatch.setenv("TESTO_CASE_CACHE_ROWS", "0")
    test_results.clear_cache()
    store = _store(tmp_path)
    store.replace("run-1", {"a": _case("passed")})
    store.cases("run-1")
    assert test_results._cache_rows == 0


def test_cases_for_keys_reads_only_the_named_tests(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace("r1", {"a": _case("failed"), "b": _case("passed")})
    store.replace("r2", {"b": _case("passed")})
    store.replace("r3", {"a": _case("passed", labels=[{"name": "tag", "value": "x"}])})
    store.cases("r3")  # one from the cache, the rest from the database
    found = store.cases_for_keys(["r1", "r2", "r3", "nope"], ["a"])
    assert set(found) == {"r1", "r3"}
    assert found["r1"]["a"]["status"] == "failed"
    assert found["r3"]["a"]["labels"] == [{"name": "tag", "value": "x"}]
    assert store.runs_with_rows(["r1", "r2", "r3", "nope"]) == {"r1", "r2", "r3"}
