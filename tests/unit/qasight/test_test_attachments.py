"""A test's attachments in one run (qasight/services/test_attachments.py)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import test_attachments


def _run(snapshot_dir: str | None) -> CompletedRunView:
    return CompletedRunView(
        run_id="run-1",
        status=RunStatus.COMPLETED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind="cycle",
        returncode=1,
        wall_duration_ms=1000.0,
        metrics_duration_ms=None,
        total_tests=None,
        passed=None,
        failed=None,
        broken=None,
        skipped=None,
        avg_case_ms=None,
        health_pct=None,
        target_repo=None,
        snapshot_dir=snapshot_dir,
        audit_json=None,
        cycle="e2e",
    )


@pytest.fixture
def snapshot(monkeypatch, tmp_path: Path) -> Path:  # noqa: ANN001
    monkeypatch.setattr(test_attachments.paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(
        test_attachments.paths, "STATIC_HISTORY_ROOT", tmp_path / "static" / "history"
    )
    results = tmp_path / "static/history/run-1/artifacts/e2e/allure-results/playwright"
    results.mkdir(parents=True)
    for name in ("a-attachment.png", "b-attachment.zip", "c-attachment.webm", "d-attachment.txt"):
        (results / name).write_bytes(b"x" * 10)
    result = {
        "historyId": "login",
        "name": "login fails",
        "status": "failed",
        # The last attempt's files are on the test; every attempt's are on its step.
        "attachments": [
            {"name": "screenshot", "source": "a-attachment.png", "type": "image/png"},
            {"name": "trace", "source": "b-attachment.zip", "type": "application/zip"},
        ],
        "steps": [
            {
                "name": "Attempt 1",
                "attachments": [
                    {"name": "video", "source": "c-attachment.webm", "type": "video/webm"},
                    {"name": "screenshot", "source": "a-attachment.png", "type": "image/png"},
                    {"name": "gone", "source": "missing-attachment.png", "type": "image/png"},
                    {"name": "escape", "source": "../../../secret.txt", "type": "text/plain"},
                    {"name": "stdout", "source": "d-attachment.txt", "type": "text/plain"},
                ],
            }
        ],
    }
    (results / "r-result.json").write_text(json.dumps(result), encoding="utf-8")
    (results / "other-result.json").write_text(
        json.dumps({"historyId": "other", "attachments": []}), encoding="utf-8"
    )
    return results


def test_attachments_of_the_test_newest_attempt_first(snapshot: Path) -> None:
    run = _run("static/history/run-1/artifacts")
    found = test_attachments.attachments_for(run, "login")
    assert [(a.name, a.kind) for a in found] == [
        ("screenshot", "image"),
        ("trace", "trace"),
        ("video", "video"),
        ("stdout", "text"),
    ]
    assert found[0].url == (
        "history/run-1/artifacts/e2e/allure-results/playwright/a-attachment.png"
    )
    assert found[0].size_bytes == 10


def test_nothing_outside_the_history_tree_or_for_other_tests(snapshot: Path) -> None:
    assert test_attachments.attachments_for(_run("static/history/run-1/artifacts"), "other") == ()
    assert test_attachments.attachments_for(_run("elsewhere"), "login") == ()
    assert test_attachments.attachments_for(_run(None), "login") == ()
    assert test_attachments.attachments_for(replace(_run("../outside")), "login") == ()


def test_a_zip_that_isnt_a_trace_is_a_plain_file() -> None:
    assert test_attachments._kind("logs", "application/zip") == "file"
    assert test_attachments._kind("trace", "application/zip") == "trace"
    assert test_attachments._kind("data", "application/octet-stream") == "file"
