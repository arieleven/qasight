"""Environments per run (2026-11-02): what a run tested on, kept apart by flaky detection."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testo_core.cli.app import app
from testo_core.services import flaky_service, run_environment
from testo_core.services.test_history import TestPoint, _by_environment

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def test_parse_and_canonical_sort_and_clean_keys() -> None:
    assert run_environment.parse("OS=Linux, browser = firefox ,bad, =x") == {
        "browser": "firefox",
        "os": "Linux",
    }
    assert run_environment.canonical({"os": "linux", "Browser": "fire,fox"}) == (
        "browser=fire fox, os=linux"
    )
    assert run_environment.canonical({}) == ""


def test_strict_pairs_reject_a_flag_without_equals() -> None:
    with pytest.raises(run_environment.InvalidEnvironmentPair):
        run_environment.from_pairs(["firefox"], strict=True)


def test_resolve_prefers_explicit_then_testo_env_then_allure(tmp_path: Path) -> None:
    results = tmp_path / "api" / "allure-results"
    results.mkdir(parents=True)
    (results / "environment.properties").write_text(
        "# Allure's own file\nBrowser=Chrome\nos.name: Linux\n", encoding="utf-8"
    )
    assert run_environment.resolve(None, {}, tmp_path) == "browser=Chrome, os.name=Linux"
    assert run_environment.resolve(None, {"TESTO_ENV": "os=mac"}, tmp_path) == "os=mac"
    assert run_environment.resolve("browser=firefox", {"TESTO_ENV": "os=mac"}, tmp_path) == (
        "browser=firefox"
    )
    assert run_environment.resolve(None, {}, tmp_path / "missing") == ""


def test_cli_pairs_merge_into_testo_env() -> None:
    environ = {"TESTO_ENV": "os=linux, browser=chromium"}
    run_environment.apply_cli_pairs(["browser=firefox"], environ)
    assert environ["TESTO_ENV"] == "browser=firefox, os=linux"
    run_environment.apply_cli_pairs([], environ)
    assert environ["TESTO_ENV"] == "browser=firefox, os=linux"


def test_cli_rejects_a_bad_env_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TESTO_ENV", raising=False)
    result = CliRunner().invoke(app, ["ingest", "x.xml", "--env", "firefox", "--no-persist"])
    assert result.exit_code == 2
    assert "expected KEY=VALUE" in result.output
    assert "TESTO_ENV" not in os.environ


def _obs(status: str, env: str | None, commit: str = "") -> dict[str, object]:
    return {"status": status, "name": "t", "branch": "main", "commit": commit, "environment": env}


def test_failing_on_one_environment_is_not_flaky() -> None:
    # Alternating runs: always fails on firefox, always passes on chromium.
    observations = [
        _obs("failed" if i % 2 else "passed", "browser=firefox" if i % 2 else "browser=chromium")
        for i in range(8)
    ]
    assert flaky_service.classify("t", observations) is None
    # Without environments the same history reads as flipping.
    flat = [{**o, "environment": None} for o in observations]
    assert flaky_service.classify("t", flat) is not None


def test_same_commit_on_two_environments_is_not_a_conflict() -> None:
    observations = [
        _obs("passed", "browser=chromium", commit="abc"),
        _obs("failed", "browser=firefox", commit="abc"),
    ]
    assert flaky_service._same_commit_conflicts(observations) == 0
    assert flaky_service._same_commit_conflicts([{**o, "environment": None} for o in observations])


def _point(status: str, env: str | None) -> TestPoint:
    return TestPoint(
        run_id="r",
        created_at=0.0,
        status=status,
        duration_ms=None,
        message="",
        branch=None,
        environment=env,
    )


def test_test_history_splits_outcomes_by_environment() -> None:
    points = (
        _point("failed", "browser=firefox"),
        _point("passed", "browser=chromium"),
        _point("failed", "browser=firefox"),
        _point("passed", None),
    )
    split = _by_environment(points)
    assert [(e.environment, e.runs, e.passed, e.failed, e.last_status) for e in split] == [
        ("browser=firefox", 2, 0, 2, "failed"),
        ("(not set)", 1, 1, 0, "passed"),
        ("browser=chromium", 1, 1, 0, "passed"),
    ]
    assert _by_environment((_point("passed", None),)) == ()
