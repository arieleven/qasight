"""``test_result`` rows: one test's outcome in one run, read by the analytics instead of files."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from testo_core.repository import migrations
from testo_core.repository.test_results import TestResultStore

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
