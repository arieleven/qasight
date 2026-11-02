"""Stability per owner (services/team_view.py) and suite growth and hygiene (services/suite_hygiene.py)."""

from __future__ import annotations

from typing import Any

import pytest

from testo_core.services.suite_hygiene import build_hygiene
from testo_core.services.team_view import NO_OWNER, build_teams, label_owners
from tests.unit.testo_core.test_delivery_metrics import DAY, NOW, START, _run, cases  # noqa: F401


def _case(status: str, ms: int = 100, owner: str | None = None) -> dict[str, Any]:
    labels = [{"name": "owner", "value": owner}] if owner else []
    return {"name": "", "status": status, "duration_ms": ms, "labels": labels}


def _set(table: dict[str, Any], run_id: str, **tests: dict[str, Any]) -> None:
    table[run_id] = {k: {**v, "name": k} for k, v in tests.items()}


def test_label_owners() -> None:
    assert label_owners(
        {"labels": [{"name": "owner", "value": "@pay"}, {"name": "tag", "value": "x"}]}
    ) == ("@pay",)
    assert label_owners({}) == ()


def test_teams_split_failures_fixes_and_flaky_by_owner(cases: dict[str, Any]) -> None:  # noqa: F811
    runs = [_run(f"r{i}", 6 - i, red=i in (0, 1, 2, 4)) for i in range(6)]
    # test_pay fails r0..r1 and is fixed in r2 by @pay; test_cart fails from r4 on (open, @shop);
    # test_flip flips (flaky, no owner); test_ok passes.
    plan = {
        "test_pay": ["failed", "failed", "passed", "passed", "passed", "passed"],
        "test_cart": ["passed", "passed", "passed", "passed", "failed", "failed"],
        "test_flip": ["passed", "failed", "failed", "passed", "failed", "passed"],
        "test_ok": ["passed"] * 6,
    }
    owners = {"test_pay": "@pay", "test_cart": "@shop", "test_ok": "@shop"}
    for i in range(6):
        _set(cases, f"r{i}", **{k: _case(v[i], owner=owners.get(k)) for k, v in plan.items()})
    # runs 5 is red because test_cart fails
    teams = build_teams(runs, start=START, end=NOW, owners=lambda cycle, case: label_owners(case))
    assert teams.owners_known
    rows = {r.owner: r for r in teams.rows}
    assert [r.owner for r in teams.rows] == ["@shop", "@pay", NO_OWNER]
    assert (rows["@shop"].tests, rows["@shop"].failing, rows["@shop"].open_failures) == (2, 1, 1)
    assert rows["@shop"].oldest_open_s == pytest.approx(1 * DAY)
    assert (rows["@pay"].fixes, rows["@pay"].median_fix_s) == (1, pytest.approx(2 * DAY))
    assert rows[NO_OWNER].flaky == 1


def test_hygiene_growth_skipped_and_slow(cases: dict[str, Any]) -> None:  # noqa: F811
    runs = [_run("o1", 9), _run("n1", 3), _run("n2", 2), _run("n3", 1)]
    base = {f"t{i}": _case("passed", ms=100) for i in range(10)}
    _set(cases, "o1", **base, test_old=_case("passed"), test_slow=_case("passed", 9000))
    for rid in ("n1", "n2", "n3"):
        _set(
            cases,
            rid,
            **base,
            test_new=_case("passed"),
            test_skip=_case("skipped"),
            test_slow=_case("passed", 9000),
        )
    h = build_hygiene(runs, start=START, end=NOW)
    assert (h.tests, h.prev_tests, h.added, h.removed) == (13, 12, 2, 1)
    assert sorted(t.name for t in h.added_tests) == ["test_new", "test_skip"]
    assert [t.name for t in h.removed_tests] == ["test_old"]
    assert [t.name for t in h.always_skipped] == ["test_skip"]
    assert [t.name for t in h.slow_never_failed] == ["test_slow"]
    assert h.slow_never_failed[0].detail == "9.0s, passed all 4 runs"
    (size,) = h.cycles
    assert (size.cycle, size.tests, size.prev_tests) == ("api", 13, 12)


def test_hygiene_new_cycle_counts_from_zero(cases: dict[str, Any]) -> None:  # noqa: F811
    runs = [_run("a1", 9, cycle="api"), _run("a2", 2, cycle="api"), _run("u1", 1, cycle="ui")]
    _set(cases, "a1", t1=_case("passed"))
    _set(cases, "a2", t1=_case("passed"))
    _set(cases, "u1", u1=_case("passed"), u2=_case("passed"))
    h = build_hygiene(runs, start=START, end=NOW)
    assert (h.tests, h.prev_tests) == (3, 1)
