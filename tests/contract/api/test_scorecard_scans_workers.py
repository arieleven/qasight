"""Scorecard scans across API workers: state in the database, one scan at a time everywhere."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import create_engine

from qasight_api.scorecard_scans import STALE_AFTER_S, ScanBusyError, ScorecardScanManager

pytestmark = [pytest.mark.contract, pytest.mark.tier_fast]


def _request(target: str) -> SimpleNamespace:
    return SimpleNamespace(
        target=target, tools=[], project=None, version=None, ref=None, url=None, openapi=None
    )


@pytest.fixture
def workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    first, second = ScorecardScanManager(engine), ScorecardScanManager(engine)
    # Keep the scan "running" so the second worker sees it in progress.
    monkeypatch.setattr(ScorecardScanManager, "_run", lambda self, state, request, only: None)
    return first, second


def test_another_worker_reports_a_scan_and_refuses_a_second(workers) -> None:
    first, second = workers
    state = first.start(_request("acme/app"))
    seen = second.get(state.id)
    assert seen is not None and seen.state == "running" and seen.target == "acme/app"
    with pytest.raises(ScanBusyError):
        second.start(_request("acme/other"))


def test_progress_and_the_end_are_seen_by_every_worker(workers) -> None:
    first, second = workers
    state = first.start(_request("acme/app"))
    first._event(state, {"type": "plan", "languages": ["python"], "skipped": {}, "tools": ["ruff"]})
    first._event(state, {"type": "tool", "tool": "ruff", "status": "done", "score": 90})
    state.state, state.finished_at = "done", time.time()
    first._save(state)
    seen = second.get(state.id)
    assert seen is not None
    assert seen.state == "done" and seen.languages == ["python"]
    assert seen.to_dict()["tools"] == [
        {"tool": "ruff", "status": "done", "score": 90, "message": ""}
    ]
    second.start(_request("acme/next"))  # no longer busy


def test_a_scan_whose_worker_died_stops_blocking(workers, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = workers
    state = first.start(_request("acme/app"))
    later = time.time() + STALE_AFTER_S + 60
    monkeypatch.setattr("qasight_api.scorecard_scans.time.time", lambda: later)
    second.start(_request("acme/app"))
    old = second.get(state.id)
    assert old is not None and old.state == "failed"
