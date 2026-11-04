"""Timing-based shards: ``plan_split``, ``qasight split`` and ``pytest --qasight-shard``."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.services import test_split
from qasight.services.test_split import (
    SplitError,
    TestTime,
    nodeid_names,
    parse_shard,
    plan_split,
    timings_index,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def test_parse_shard() -> None:
    assert parse_shard("2/4") == (2, 4)
    assert parse_shard(" 1 / 1 ") == (1, 1)
    for bad in ("0/4", "5/4", "2", "a/b", "1/0", ""):
        with pytest.raises(SplitError):
            parse_shard(bad)


def test_nodeid_names_match_junit_and_allure_names() -> None:
    assert nodeid_names("tests/a/test_x.py::TestC::test_y[1-a]") == (
        "tests.a.test_x.TestC.test_y[1-a]",
        "tests.a.test_x.TestC#test_y",
        "tests/a/test_x.py::TestC::test_y[1-a]",
    )
    assert nodeid_names("test_x.py::test_z")[:2] == ("test_x.test_z", "test_x#test_z")
    assert nodeid_names("Suite.case") == ("Suite.case",)


def test_split_balances_time_and_keeps_order_inside_a_shard() -> None:
    tests = ["a", "b", "c", "d", "e", "f"]
    timings = {"a": 5.0, "b": 4.0, "c": 3.0, "d": 3.0, "e": 2.0, "f": 1.0}
    plan = plan_split(tests, timings, 2)
    assert [s.estimate_ms for s in plan.shards] == [9.0, 9.0]
    assert [s.tests for s in plan.shards] == [("a", "d", "f"), ("b", "c", "e")]
    assert sorted(t for s in plan.shards for t in s.tests) == tests
    for shard in plan.shards:
        assert list(shard.tests) == sorted(shard.tests)
    assert (plan.timed, plan.total) == (6, 6)


def test_split_does_not_depend_on_the_order_the_tests_came_in() -> None:
    tests = [f"t{i:02d}" for i in range(40)]
    timings = {t: float(i % 7) for i, t in enumerate(tests)}
    expected = [set(s.tests) for s in plan_split(tests, timings, 3).shards]
    shuffled = tests[:]
    random.Random(4).shuffle(shuffled)
    assert [set(s.tests) for s in plan_split(shuffled, timings, 3).shards] == expected


def test_no_history_is_round_robin_and_unknown_tests_weigh_the_median() -> None:
    plan = plan_split([f"t{i}" for i in range(7)], {}, 3)
    assert [len(s.tests) for s in plan.shards] == [3, 2, 2]
    assert plan.timed == 0 and all(s.estimate_ms == 0 for s in plan.shards)
    # "new" has no time: it counts as the median (10), so it isn't piled onto one shard.
    plan = plan_split(["a", "b", "c", "new"], {"a": 30.0, "b": 10.0, "c": 5.0}, 2)
    assert sorted(s.estimate_ms for s in plan.shards) == [25.0, 30.0]


def test_more_shards_than_tests_leaves_some_empty() -> None:
    plan = plan_split(["a"], {}, 3)
    assert [len(s.tests) for s in plan.shards] == [1, 0, 0]
    with pytest.raises(SplitError):
        plan_split(["a"], {}, 0)


def test_timings_from_the_team_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    seen: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "items": [
                    {
                        "key": "k1",
                        "full_name": "m.test_a",
                        "stage": "unit",
                        "median_duration_ms": 40,
                    },
                    {"key": "k2", "full_name": "m.test_b", "stage": "ui", "median_duration_ms": 90},
                    {"key": "k3", "full_name": "m.test_c", "stage": "unit"},
                ]
            }

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        seen.update(url=url, **kwargs)
        return _Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    times = test_split.load_test_times(
        "app", stage="unit", server="https://t.example/", token="tok"
    )
    assert seen["url"] == "https://t.example/api/v1/analytics/tests"
    assert seen["params"]["cycle"] == "app" and seen["headers"]["Authorization"] == "Bearer tok"
    assert [t.name for t in times] == ["m.test_a", "m.test_c"]
    assert timings_index(times) == {"m.test_a": 40.0, "k1": 40.0}


def _fake_times(monkeypatch: pytest.MonkeyPatch, times: list[TestTime]) -> None:
    monkeypatch.setattr(test_split, "load_test_times", lambda *a, **k: times)


def test_cli_prints_one_shard_and_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_times(
        monkeypatch,
        [
            TestTime(name="pkg.test_x.test_slow", key="1", stage="s", median_ms=900.0),
            TestTime(name="pkg.test_x.test_fast", key="2", stage="s", median_ms=10.0),
        ],
    )
    collected = tmp_path / "tests.txt"
    collected.write_text(
        "pkg/test_x.py::test_slow\npkg/test_x.py::test_fast\npkg/test_x.py::test_new\n\n"
        "3 tests collected in 0.01s\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    first = runner.invoke(
        app, ["split", "--cycle", "c", "--shards", "2", "--index", "1", "--tests", str(collected)]
    )
    second = runner.invoke(
        app, ["split", "--cycle", "c", "--shards", "2", "--index", "2", "--tests", str(collected)]
    )
    assert first.exit_code == 0 and second.exit_code == 0, first.output + second.output
    assert first.output.split() == ["pkg/test_x.py::test_slow"]
    assert second.output.split() == ["pkg/test_x.py::test_fast", "pkg/test_x.py::test_new"]

    out = runner.invoke(app, ["split", "--cycle", "c", "--shards", "2", "--json"])
    payload = json.loads(out.output)
    assert payload["total"] == 2 and payload["timed"] == 2
    assert [s["tests"] for s in payload["shards"]] == [
        ["pkg.test_x.test_slow"],
        ["pkg.test_x.test_fast"],
    ]

    bad = runner.invoke(app, ["split", "--cycle", "c", "--shards", "2", "--index", "3"])
    assert bad.exit_code == 2


def test_cli_with_nothing_to_split(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_times(monkeypatch, [])
    result = CliRunner().invoke(app, ["split", "--cycle", "c", "--shards", "2"])
    assert result.exit_code == 1
    assert "No tests to split" in result.output


def _suite(tmp_path: Path) -> None:
    (tmp_path / "suite").mkdir()
    (tmp_path / "suite" / "test_many.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('n', range(9))\n"
        "def test_n(n): pass\n"
        "class TestK:\n"
        "    def test_one(self): pass\n"
        "    def test_two(self): pass\n",
        encoding="utf-8",
    )


def _pytest(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
        # An empty history: the split is round robin, the same in every process.
        "DATABASE_URL": f"sqlite:///{tmp_path / 'history.db'}",
    }
    env.pop("TESTO_SHARD", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--no-cov",
            "-rA",
            *args,
            "suite",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _passed(output: str) -> set[str]:
    return {line.split()[1] for line in output.splitlines() if line.startswith("PASSED ")}


@pytest.mark.parametrize("workers", [None, "2"])
def test_pytest_shards_cover_every_test_once(tmp_path: Path, workers: str | None) -> None:
    if workers:
        pytest.importorskip("xdist")
    _suite(tmp_path)
    extra = ["-n", workers] if workers else ["-p", "no:xdist"]
    runs = [_pytest(tmp_path, *extra, "--qasight-shard", f"{i}/3") for i in (1, 2, 3)]
    for done in runs:
        assert done.returncode == 0, done.stdout + done.stderr
    shards = [_passed(done.stdout) for done in runs]
    assert sum(len(s) for s in shards) == 11
    assert len(set().union(*shards)) == 11
    assert all(len(s) in (3, 4) for s in shards)
    assert "qasight: shard 2/3 " in runs[1].stdout


def test_pytest_bad_shard_is_a_usage_error(tmp_path: Path) -> None:
    _suite(tmp_path)
    done = _pytest(tmp_path, "-p", "no:xdist", "--qasight-shard", "4/3")
    assert done.returncode == 4
    assert "--qasight-shard" in done.stderr


def test_pytest_shards_use_the_times_pytest_qasight_recorded(tmp_path: Path) -> None:
    (tmp_path / "suite").mkdir()
    (tmp_path / "suite" / "test_t.py").write_text(
        "import time\n"
        "def test_slow(): time.sleep(0.4)\n"
        + "".join(f"def test_{c}(): time.sleep(0.05)\n" for c in "abcde"),
        encoding="utf-8",
    )
    recorded = _pytest(tmp_path, "-p", "no:xdist", "--qasight", "--qasight-cycle", "sh")
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    runs = [
        _pytest(tmp_path, "-p", "no:xdist", "--qasight-shard", f"{i}/2", "--qasight-timings", "sh")
        for i in (1, 2)
    ]
    shards = [_passed(done.stdout) for done in runs]
    # The slow test gets a shard of its own (plus at most one quick one); the rest share the other.
    slow = next(s for s in shards if "suite/test_t.py::test_slow" in s)
    assert len(slow) <= 2 and sum(len(s) for s in shards) == 6
    assert "times known for 6 tests" in runs[0].stdout
