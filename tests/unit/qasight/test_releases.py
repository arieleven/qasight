"""Releases (2026-11-02): runs recorded with ``--release`` / ``TESTO_RELEASE``, grouped
on the Releases page with each release's failing, flaky and fixed tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.history.views import CompletedRunView
from qasight.services import ingest_client, releases, run_release

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


# --- run_release -------------------------------------------------------------------


@pytest.mark.parametrize("value", ["2.3", "v2.3.0-rc1", "2026.11", "1.0+build_7"])
def test_good_release_names_are_kept(value: str) -> None:
    assert run_release.clean(f"  {value} ", strict=True) == value


@pytest.mark.parametrize("value", ["../x", "a/b", "two words", "-x", "x" * 61])
def test_bad_release_names_are_dropped_or_refused(value: str) -> None:
    assert run_release.clean(value) == ""
    with pytest.raises(run_release.InvalidRelease):
        run_release.clean(value, strict=True)


def test_explicit_release_wins_over_the_variable() -> None:
    assert run_release.resolve("2.4", {"TESTO_RELEASE": "2.3"}) == "2.4"
    assert run_release.resolve(None, {"TESTO_RELEASE": "2.3"}) == "2.3"
    assert run_release.resolve("", {}) == ""


def test_cli_rejects_a_bad_release_with_exit_2() -> None:
    with patch.dict(os.environ, {}, clear=False):
        result = CliRunner().invoke(app, ["run", "--cycle", "x", "--release", "a/b"])
    assert result.exit_code == 2
    assert "--release" in result.output


def test_ingest_client_sends_the_release() -> None:
    assert ingest_client.client_run_metadata({"TESTO_RELEASE": "2.3"})["release"] == "2.3"
    assert "release" not in ingest_client.client_run_metadata({"TESTO_RELEASE": "a b"})


def test_cli_ingest_records_the_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TESTO_RELEASE", "old")  # restored after the test
    (tmp_path / "r.xml").write_text(
        '<testsuite name="s"><testcase classname="s" name="t" time="0.1"/></testsuite>',
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app, ["ingest", "r.xml", "--cycle", "web", "--no-persist", "--release", "2.3"]
    )
    assert result.exit_code == 0, result.output
    assert os.environ.get("TESTO_RELEASE") == "2.3"


# --- releases ----------------------------------------------------------------------


def _run(
    run_id: str, cycle: str, release: str | None, at: float, passed: int, failed: int
) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=None,
        created_at=at,
        started_at=at,
        finished_at=at + 1,
        test_kind="unit",
        returncode=1 if failed else 0,
        wall_duration_ms=1000.0,
        metrics_duration_ms=None,
        total_tests=passed + failed,
        passed=passed,
        failed=failed,
        broken=0,
        skipped=0,
        avg_case_ms=None,
        health_pct=None,
        target_repo=None,
        snapshot_dir=None,
        audit_json=None,
        cycle=cycle,
        branch="main",
        commit=run_id,
        release=release,
    )


def _case(status: str, message: str = "") -> dict[str, Any]:
    return {"name": "", "status": status, "message": message, "duration_ms": 5}


@pytest.fixture
def history(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = {
        "api": [  # newest first
            _run("a4", "api", "2.4", 40, 1, 1),
            _run("a3", "api", "2.4", 30, 2, 0),
            _run("a2", "api", None, 25, 2, 0),
            _run("a1", "api", "2.3", 10, 1, 1),
        ],
        "web": [_run("w1", "web", "2.4", 35, 1, 0)],
    }
    cases = {
        "a1": {
            "pay": _case("failed", "boom\nmore"),
            "cart": _case("passed"),
            "login": _case("passed"),
        },
        "a3": {"pay": _case("passed"), "cart": _case("passed"), "login": _case("passed")},
        "a4": {"pay": _case("passed"), "cart": _case("failed", "nope"), "login": _case("passed")},
        "w1": {"home": _case("passed")},
    }
    monkeypatch.setattr(
        releases, "latest_run_per_cycle", lambda: {c: rs[0] for c, rs in runs.items()}
    )
    monkeypatch.setattr(releases, "list_cycle_runs", lambda cycle: runs[cycle])
    monkeypatch.setattr(releases, "cases_for_run", lambda run: cases.get(run.run_id, {}))


@pytest.mark.usefixtures("history")
def test_releases_are_listed_newest_first_with_latest_run_totals() -> None:
    listed = releases.list_releases()
    assert [r.release for r in listed] == ["2.4", "2.3"]
    latest = listed[0]
    assert (latest.runs, latest.cycles) == (3, 2)
    # a4 (1 passed, 1 failed) + w1 (1 passed): the latest run of each cycle.
    assert (latest.total, latest.passed, latest.failed) == (3, 2, 1)
    assert latest.pass_rate == pytest.approx(66.7)


@pytest.mark.usefixtures("history")
def test_a_release_shows_new_failures_and_what_it_fixed() -> None:
    detail = releases.load_release("2.4")
    assert detail is not None
    assert detail.previous is not None and detail.previous.release == "2.3"
    assert [(c.cycle, c.run_id, c.runs) for c in detail.cycles] == [
        ("api", "a4", 2),
        ("web", "w1", 1),
    ]
    assert [(t.key, t.new, t.message) for t in detail.failing] == [("cart", True, "nope")]
    assert [t.key for t in detail.fixed] == ["pay"]


@pytest.mark.usefixtures("history")
def test_the_first_release_has_nothing_to_compare_with() -> None:
    detail = releases.load_release("2.3")
    assert detail is not None and detail.previous is None
    assert [(t.key, t.new, t.message) for t in detail.failing] == [("pay", False, "boom")]
    assert releases.load_release("9.9") is None
