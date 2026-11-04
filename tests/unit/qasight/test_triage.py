"""Triage memory: a failure's cause, marked once (qasight/services/triage.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import create_engine

from qasight.services.triage import TriageError, TriageStore


@pytest.fixture
def store(tmp_path: Path) -> TriageStore:
    return TriageStore(create_engine(f"sqlite:///{tmp_path / 'history.db'}"))


def test_failures_with_the_same_message_inherit_the_cause(store: TriageStore) -> None:
    marked = store.mark(
        cycle="api",
        message="TimeoutError: waited 30.5s for 'db-7'",
        cause="environment",
        note="  shared DB is slow  ",
        run_id="run-1",
        test_key="test_orders",
        marked_by="ada",
    )
    assert marked.signature == "timeouterror: waited …s for …"
    assert marked.note == "shared DB is slow"

    found = store.for_messages(
        "api",
        ["TimeoutError: waited 12s for 'db-2'", "AssertionError: 1 != 2", ""],
    )
    assert list(found) == ["TimeoutError: waited 12s for 'db-2'"]
    triage = found["TimeoutError: waited 12s for 'db-2'"]
    assert (triage.cause, triage.run_id, triage.test_key, triage.marked_by) == (
        "environment",
        "run-1",
        "test_orders",
        "ada",
    )
    # Causes belong to one cycle.
    assert store.for_messages("web", ["TimeoutError: waited 12s for 'db-2'"]) == {}


def test_marking_again_replaces_and_clear_forgets(store: TriageStore) -> None:
    store.mark(cycle="api", message="boom 1", cause="flaky")
    again = store.mark(cycle="api", message="boom 2", cause="product_bug", note="#42")
    assert [(t.cause, t.note) for t in store.list("api")] == [("product_bug", "#42")]
    assert store.list("web") == []

    assert store.clear(cycle="api", signature=again.signature) is True
    assert store.clear(cycle="api", signature=again.signature) is False
    assert store.for_messages("api", ["boom 3"]) == {}


@pytest.mark.parametrize(
    ("cycle", "message", "cause"),
    [("api", "boom", "gremlins"), ("api", "   ", "flaky"), ("", "boom", "flaky")],
)
def test_rejects_unknown_causes_and_empty_messages(
    store: TriageStore, cycle: str, message: str, cause: str
) -> None:
    with pytest.raises(TriageError):
        store.mark(cycle=cycle, message=message, cause=cause)
