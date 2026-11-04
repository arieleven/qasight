"""Tests for Allure history injection from prior archived runs."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from qasight.reporting.history_inject import try_inject_prior_history
from qasight.repository.db import get_report_archive_repository, reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight.services.report_archive import build_cycle_zip_bytes


@pytest.fixture
def sqlite_report_repo(monkeypatch: pytest.MonkeyPatch):
    reset_repository_cache()
    reset_engine_cache()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    try:
        yield get_report_archive_repository()
    finally:
        reset_repository_cache()
        reset_engine_cache()


def _write_min_cycle(root: Path, plan: str, *, with_history: bool) -> None:
    plan_dir = root / plan
    plan_dir.mkdir(parents=True)
    (plan_dir / "events.ndjson").write_text("{}\n", encoding="utf-8")
    (plan_dir / "plan_result.json").write_text(
        json.dumps({"plan": plan, "exit_code": 0, "duration_s": 1.0}),
        encoding="utf-8",
    )
    res = plan_dir / "st" / "allure-results" / "pytest"
    res.mkdir(parents=True)
    (res / "t-result.json").write_text(
        '{"historyId":"h1","status":"passed","name":"t","start":1,"stop":2}',
        encoding="utf-8",
    )
    if with_history:
        hist = res / "history"
        hist.mkdir(parents=True)
        (hist / "snapshot.json").write_text('{"from":"prior"}', encoding="utf-8")


def test_try_inject_prior_history_noop_when_single_archive(
    tmp_path: Path,
    sqlite_report_repo,
) -> None:
    root = tmp_path / "a1"
    _write_min_cycle(root, "c1", with_history=True)
    blob, summary, _ec = build_cycle_zip_bytes(root, "c1")
    sqlite_report_repo.insert(
        cycle_name="c1",
        exit_code=0,
        summary_json=summary,
        artifact_bytes=blob,
    )

    live = tmp_path / "live"
    _write_min_cycle(live, "c1", with_history=False)
    res_dir = live / "c1" / "st" / "allure-results" / "pytest"
    assert not (res_dir / "history").exists()

    try_inject_prior_history(
        artifacts_root=live,
        plan_name="c1",
        console=None,
        enabled=True,
    )
    assert not (res_dir / "history").exists()


def test_try_inject_prior_history_copies_from_second_newest(
    tmp_path: Path,
    sqlite_report_repo,
) -> None:
    older_root = tmp_path / "older"
    _write_min_cycle(older_root, "c1", with_history=True)
    older_blob, older_summary, _ = build_cycle_zip_bytes(older_root, "c1")

    newer_root = tmp_path / "newer"
    _write_min_cycle(newer_root, "c1", with_history=False)
    newer_blob, newer_summary, _ = build_cycle_zip_bytes(newer_root, "c1")

    sqlite_report_repo.insert(
        cycle_name="c1",
        exit_code=0,
        summary_json=older_summary,
        artifact_bytes=older_blob,
    )
    time.sleep(0.02)
    sqlite_report_repo.insert(
        cycle_name="c1",
        exit_code=0,
        summary_json=newer_summary,
        artifact_bytes=newer_blob,
    )

    live = tmp_path / "live"
    _write_min_cycle(live, "c1", with_history=False)
    res_dir = live / "c1" / "st" / "allure-results" / "pytest"

    console = MagicMock()
    try_inject_prior_history(
        artifacts_root=live,
        plan_name="c1",
        console=console,
        enabled=True,
    )
    assert (res_dir / "history" / "snapshot.json").is_file()
    assert "prior" in (res_dir / "history" / "snapshot.json").read_text(encoding="utf-8")
    console.print.assert_called()
