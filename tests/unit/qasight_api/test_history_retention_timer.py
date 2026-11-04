"""The API applies ``history.retention`` on a timer, and only when the section is set."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from qasight.config.history_section import HistoryRetention
from qasight.history import retention

pytestmark = pytest.mark.tier_fast

main = pytest.importorskip("qasight_api.main")


def _write_config(tmp_path: Path, history: str) -> None:
    (tmp_path / "qasight.yaml").write_text(
        "version: 1\ncycles:\n  unit:\n    stages:\n      - name: unit\n        equipment: pytest\n"
        + history,
        encoding="utf-8",
    )


def test_no_section_starts_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_config(tmp_path, "")
    monkeypatch.chdir(tmp_path)
    before = {t.name for t in threading.enumerate()}

    stop = main._start_retention_timer()
    stop()

    assert "testo-history-retention" not in {t.name for t in threading.enumerate()} - before


def test_no_config_at_all_starts_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # an ingest-only server has no qasight.yaml
    main._start_retention_timer()()


def test_section_prunes_right_away_then_waits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_config(tmp_path, "history:\n  retention:\n    days: 30\n    runs_per_cycle: 5\n")
    monkeypatch.chdir(tmp_path)
    calls: list[HistoryRetention] = []
    pruned = threading.Event()

    def fake_prune(limits: HistoryRetention, *, dry_run: bool = False) -> retention.PruneReport:
        calls.append(limits)
        pruned.set()
        return retention.PruneReport()

    monkeypatch.setattr(retention, "prune_history", fake_prune)

    stop = main._start_retention_timer()
    try:
        assert pruned.wait(5), "the timer did not prune on start"
    finally:
        stop()

    assert calls == [HistoryRetention(days=30, runs_per_cycle=5)]
