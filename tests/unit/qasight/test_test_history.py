"""One test across a cycle's runs (qasight/services/test_history.py)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from qasight import paths
from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import flaky_service, test_history
from tests._history_fakes import cycle_runs_from


def _run(run_id: str, *, created_at: float, cycle: str = "unit") -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at + 1,
        test_kind="cycle",
        returncode=0,
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
        snapshot_dir=f"static/history/{run_id}/artifacts",
        audit_json=None,
        cycle=cycle,
    )


def _write(root: Path, run: CompletedRunView, results: dict[str, tuple[str, int, str]]) -> None:
    folder = root / str(run.snapshot_dir) / "api" / "allure-results" / "pytest"
    folder.mkdir(parents=True, exist_ok=True)
    for name, (status, ms, message) in results.items():
        body: dict[str, object] = {
            "historyId": name,
            "name": name,
            "fullName": f"tests.api.{name}",
            "status": status,
            "start": 1_000,
            "stop": 1_000 + ms,
        }
        if message:
            body["statusDetails"] = {"message": message}
        (folder / f"{name}-result.json").write_text(json.dumps(body), encoding="utf-8")


@pytest.fixture
def six_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[CompletedRunView]:
    """Six runs of ``unit`` (newest first): test_slow got slower in the last three, test_flip flips."""
    runs = [_run(f"r{i}", created_at=float(i)) for i in range(6, 0, -1)]
    runs[0] = replace(runs[0], branch="main")
    runs.insert(1, _run("other", created_at=5.5, cycle="nightly"))
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(flaky_service, "list_cycle_runs", cycle_runs_from(runs))
    unit_runs = [r for r in runs if r.cycle == "unit"]
    for index, run in enumerate(reversed(unit_runs)):  # oldest first
        slow_ms = 100 if index < 3 else 400
        flip = "failed" if index % 2 else "passed"
        _write(
            tmp_path,
            run,
            {
                "test_slow": ("passed", slow_ms, ""),
                "test_steady": ("passed", 50 + index, ""),
                "test_flip": (flip, 10, "AssertionError: 1 != 2" if flip == "failed" else ""),
            },
        )
    _write(tmp_path, runs[1], {"test_nightly_only": ("passed", 5, "")})
    return runs


def test_lists_every_test_of_the_cycle_failing_first(six_runs: list[CompletedRunView]) -> None:
    found = test_history.list_cycle_tests("unit")

    assert found.runs_considered == 6
    assert [t.name for t in found.tests] == ["test_flip", "test_slow", "test_steady"]
    flip = found.tests[0]
    assert (flip.runs, flip.passed, flip.failed, flip.flaky) == (6, 3, 3, True)
    assert flip.last_status == "failed"
    assert flip.stage == "api"


def test_got_slower_only_when_the_change_is_more_than_noise(
    six_runs: list[CompletedRunView],
) -> None:
    tests = {t.name: t for t in test_history.list_cycle_tests("unit").tests}

    assert tests["test_slow"].slowdown_ms == 300
    assert tests["test_slow"].slowdown_pct == 300.0
    assert tests["test_slow"].median_duration_ms == 250
    # 50 ms → 55 ms is under the 20 ms floor.
    assert tests["test_steady"].slowdown_ms is None


def test_search_matches_name_full_name_and_group(six_runs: list[CompletedRunView]) -> None:
    assert [t.name for t in test_history.list_cycle_tests("unit", query="SLOW").tests] == [
        "test_slow"
    ]
    assert len(test_history.list_cycle_tests("unit", query="tests.api").tests) == 3
    assert test_history.list_cycle_tests("unit", query="nope").tests == ()


def test_history_of_one_test_newest_first_with_its_last_failure(
    six_runs: list[CompletedRunView],
) -> None:
    history = test_history.load_test_history("unit", "test_flip")

    assert history is not None
    assert [p.run_id for p in history.points] == ["r6", "r5", "r4", "r3", "r2", "r1"]
    assert history.points[0].branch == "main"
    assert history.last_failure is not None
    assert history.last_failure.run_id == "r6"
    assert history.last_failure.message == "AssertionError: 1 != 2"


def test_unknown_test_has_no_history(six_runs: list[CompletedRunView]) -> None:
    assert test_history.load_test_history("unit", "test_nightly_only") is None


def test_stable_id_is_fnv1a() -> None:
    # Known FNV-1a 32-bit values; static-backend.ts must agree.
    assert test_history.stable_id("") == "811c9dc5"
    assert test_history.stable_id("a") == "e40c292c"


def test_flip_rate_rolls_over_the_last_runs() -> None:
    from qasight.services.test_history import FLIP_WINDOW, _flip_rates

    assert FLIP_WINDOW == 5
    statuses = ["passed", "passed", "failed", "passed", "skipped", "failed", "passed", "passed"]
    assert _flip_rates(statuses) == [
        None,  # one outcome so far
        0.0,
        0.5,  # pass pass fail
        0.667,  # pass pass fail pass
        0.667,  # skipped neither passes nor fails
        1.0,  # window: pass fail pass skip fail -> 3 flips in 3 changes
        1.0,  # fail pass skip fail pass
        0.667,  # pass skip fail pass pass
    ]
