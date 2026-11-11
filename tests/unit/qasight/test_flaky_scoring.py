"""Exact values from flaky scoring: classify, broken and flaky_costs (qasight/services/flaky_service.py).

These pin the numbers and words the UI and alerts show, so a changed boundary,
plural or division is caught (they were added after a mutation-testing run).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import flaky_service
from qasight.services.flaky_service import BROKEN_STREAK, FlakyCost, classify, flaky_costs


def _obs(status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "name": "t", **extra}


def _run(
    run_id: str, *, returncode: int = 1, wall_ms: float = 60000.0, branch: str | None = None
) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind="cycle",
        returncode=returncode,
        wall_duration_ms=wall_ms,
        metrics_duration_ms=None,
        total_tests=None,
        passed=None,
        failed=None,
        broken=None,
        skipped=None,
        avg_case_ms=None,
        health_pct=None,
        target_repo=None,
        snapshot_dir=None,
        audit_json=None,
        cycle="unit",
        branch=branch,
    )


# --- classify -----------------------------------------------------------------


def test_classify_counts_and_rate_on_one_lane() -> None:
    result = classify("k", [_obs(s) for s in ["passed", "failed", "passed", "failed", "skipped"]])
    assert result is not None
    assert (result.runs, result.passed, result.failed, result.flips) == (4, 2, 2, 3)
    # 3 flips over 4 outcomes in 1 lane: 3 / (4 - 1) = 1.0
    assert result.flip_rate == 1.0
    assert result.last_status == "skipped"
    assert result.history == ("passed", "failed", "passed", "failed", "skipped")
    assert result.reasons == ("flipped 3 times across runs",)


def test_flip_rate_is_rounded_to_three_places() -> None:
    statuses = ["passed", "failed", "passed", "passed", "passed", "passed", "passed", "passed"]
    result = classify("k", [_obs(s) for s in statuses])
    assert result is not None
    assert result.flip_rate == round(2 / 7, 3) == 0.286


def test_flip_rate_steps_subtract_one_per_lane() -> None:
    observations = [
        _obs("passed", branch="a"),
        _obs("failed", branch="a"),
        _obs("passed", branch="a"),
        _obs("passed", branch="b"),
        _obs("passed", branch="b"),
    ]
    result = classify("k", observations)
    assert result is not None
    # 2 flips, 5 outcomes, 2 lanes: 2 / 3
    assert (result.flips, result.flip_rate) == (2, 0.667)
    assert result.reasons == ("flipped 2 times on a",)


def test_a_single_retry_has_a_zero_rate_not_a_division_error() -> None:
    result = classify("k", [_obs("passed", retried=True)])
    assert result is not None
    assert (result.flips, result.flip_rate, result.runs) == (0, 0.0, 1)


def test_one_flip_per_lane_is_not_enough() -> None:
    observations = [
        _obs("passed", branch="a"),
        _obs("failed", branch="a"),
        _obs("failed", branch="b"),
        _obs("passed", branch="b"),
    ]
    assert classify("k", observations) is None


def test_flips_need_both_a_pass_and_a_fail() -> None:
    # Skipped runs flip nothing: only pass/fail outcomes count.
    assert classify("k", [_obs(s) for s in ["passed", "skipped", "passed", "skipped"]]) is None


def test_environments_are_separate_lanes() -> None:
    observations = [
        _obs("passed", branch="main", environment="chrome"),
        _obs("failed", branch="main", environment="firefox"),
        _obs("passed", branch="main", environment="chrome"),
        _obs("failed", branch="main", environment="firefox"),
    ]
    assert classify("k", observations) is None
    flipping = (
        observations
        + [_obs("passed", branch="main", environment="firefox")]
        + [_obs("failed", branch="main", environment="firefox")]
    )
    result = classify("k", flipping)
    assert result is not None
    assert result.reasons == ("flipped 2 times on main (firefox)",)


def test_reasons_use_plurals_and_keep_their_order() -> None:
    observations = [
        _obs("passed", commit="c1", retried=True),
        _obs("failed", commit="c1"),
        _obs("passed", commit="c2", retried=True),
        _obs("failed", commit="c2"),
    ]
    result = classify("k", observations)
    assert result is not None
    assert result.retried == 2
    assert result.reasons == (
        "passed on retry in 2 runs",
        "passed and failed on the same commit (2 commits)",
        "flipped 3 times across runs",
    )


def test_a_retried_failure_is_not_a_pass_on_retry() -> None:
    assert classify("k", [_obs("failed", retried=True)]) is None


def test_same_commit_in_other_environments_is_not_a_conflict() -> None:
    observations = [
        _obs("passed", commit="c", environment="linux"),
        _obs("failed", commit="c", environment="windows"),
    ]
    assert classify("k", observations) is None


def test_names_fall_back_and_are_truncated() -> None:
    long_key = "k" * 100
    result = classify(long_key, [{"status": "passed", "retried": True}])
    assert result is not None
    assert result.key == "k" * 80
    assert result.name == "k" * 100  # the key is the name, cut at 120
    assert result.group == "(ungrouped)"
    assert result.full_name == ""
    named = classify(
        "k",
        [
            {
                "status": "passed",
                "retried": True,
                "name": "n" * 200,
                "group": "g" * 200,
                "full_name": "m.n",
            }
        ],
    )
    assert named is not None
    assert (len(named.name), len(named.group), named.full_name) == (120, 100, "m.n")


def test_the_latest_observation_names_the_test() -> None:
    result = classify(
        "k",
        [_obs("passed", name="old", retried=True), _obs("passed", name="new", group="g")],
    )
    assert result is not None
    assert (result.name, result.group) == ("new", "g")


# --- broken / failing_streak -------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "streak"),
    [
        ([], 0),
        (["passed"], 0),
        (["failed"], 1),
        (["passed", "failed", "broken"], 2),
        (["failed", "skipped", "failed"], 2),  # a skip neither breaks nor extends it
        (["failed", "passed", "failed", "failed", "failed"], 3),
    ],
)
def test_failing_streak(statuses: list[str], streak: int) -> None:
    assert flaky_service.failing_streak([_obs(s) for s in statuses]) == streak


def test_broken_starts_at_the_streak_boundary() -> None:
    two = [_obs("passed")] + [_obs("failed")] * (BROKEN_STREAK - 1)
    three = [_obs("passed")] + [_obs("failed")] * BROKEN_STREAK
    assert flaky_service.broken("k", two) is None
    result = flaky_service.broken("k", three)
    assert result is not None
    assert result.failing_runs == BROKEN_STREAK == 3


def test_two_failures_in_a_row_can_still_be_flaky() -> None:
    result = classify("k", [_obs(s) for s in ["passed", "failed", "passed", "failed", "failed"]])
    assert result is not None


def test_broken_fields() -> None:
    observations = [
        _obs("failed"),
        _obs("failed"),
        {"status": "broken", "message": "boom", "full_name": "m.t", "group": "g" * 200},
    ]
    result = flaky_service.broken("k" * 90, observations)
    assert result is not None
    assert result.key == "k" * 80
    assert result.name == "k" * 90
    assert result.group == "g" * 100
    assert (result.message, result.full_name) == ("boom", "m.t")
    plain = flaky_service.broken("k", [{"status": "failed"}] * 3)
    assert plain is not None
    assert (plain.group, plain.message, plain.full_name) == ("(ungrouped)", "", "")


# --- flaky_red / flaky_costs ----------------------------------------------------------


def test_flaky_red_needs_a_red_run_failed_only_by_flaky_tests() -> None:
    cases = {"a": {"status": "failed"}, "b": {"status": "broken"}, "c": {"status": "passed"}}
    assert flaky_service.flaky_red(_run("r", returncode=0), cases, {"a", "b"}) == []
    assert flaky_service.flaky_red(_run("r"), cases, {"a", "b"}) == ["a", "b"]
    assert flaky_service.flaky_red(_run("r"), cases, {"a"}) == []
    assert flaky_service.flaky_red(_run("r"), {"c": {"status": "passed"}}, {"c"}) == []


def test_costs_count_retries_of_passed_flaky_tests_only() -> None:
    green = _run("r1", returncode=0)
    cases = {
        "a": {"status": "passed", "retried": True, "duration_ms": 1500},
        "b": {"status": "failed", "retried": True, "duration_ms": 9000},
        "c": {"status": "passed", "retried": True, "duration_ms": 7000},  # not flaky
        "d": {"status": "passed", "retried": True, "duration_ms": "slow"},
        "e": {"status": "passed", "duration_ms": 4000},  # not retried
    }
    costs = flaky_costs([(green, cases), (green, cases)], {"a", "b", "d", "e"})
    assert set(costs) == {"a", "d"}
    assert costs["a"] == FlakyCost(retries=2, retry_ms=3000)
    assert costs["d"] == FlakyCost(retries=2, retry_ms=0)


def test_costs_share_a_red_run_among_its_flaky_culprits() -> None:
    red = _run("r1", wall_ms=60000.0, branch="pr-7")
    red_main = _run("r2", wall_ms=30000.0, branch="main")
    red_nobranch = _run("r3", wall_ms=12000.0)
    both = {"a": {"status": "failed"}, "b": {"status": "failed"}}
    only_a = {"a": {"status": "failed"}}
    costs = flaky_costs(
        [(red, both), (red_main, only_a), (red_main, only_a), (red_nobranch, only_a)], {"a", "b"}
    )
    assert costs["b"] == FlakyCost(red_runs=1, red_run_ms=30000.0, branches=("pr-7",))
    assert costs["a"] == FlakyCost(
        red_runs=4, red_run_ms=30000.0 + 30000.0 * 2 + 12000.0, branches=("main", "pr-7")
    )
    assert costs["a"].minutes == round(102000 / 60000, 1) == 1.7


def test_runs_red_for_another_reason_cost_nothing() -> None:
    red = _run("r1")
    costs = flaky_costs(
        [(red, {"a": {"status": "failed"}})], {"a"}, not_flaky_runs=frozenset({"r1"})
    )
    assert costs == {}


def test_minutes_add_retries_and_red_runs() -> None:
    assert FlakyCost(retry_ms=30000, red_run_ms=60000.0).minutes == 1.5
    assert FlakyCost().minutes == 0.0


def test_report_sorts_by_cost_then_red_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    base = classify("x", [_obs("passed", retried=True)])
    assert base is not None
    found = {
        "cheap": replace(base, key="cheap", name="b"),
        "costly": replace(base, key="costly", name="a"),
    }
    monkeypatch.setattr(flaky_service, "classify", lambda key, obs: found.get(key))
    monkeypatch.setattr(flaky_service, "broken", lambda key, obs: None)
    runs = [_run("r2", wall_ms=120000.0), _run("r1", returncode=0)]
    cases = {
        "r1": {"cheap": {"status": "passed"}, "costly": {"status": "passed"}},
        "r2": {"cheap": {"status": "passed"}, "costly": {"status": "failed"}},
    }
    monkeypatch.setattr(flaky_service, "_cases_for_run", lambda run: cases[run.run_id])
    report = flaky_service._report("unit", runs, 20)
    assert [t.key for t in report.flaky] == ["costly", "cheap"]
    assert (report.flaky[0].red_runs, report.flaky[0].cost_minutes) == (1, 2.0)
    assert (report.runs_analyzed, report.tests_analyzed) == (2, 2)
