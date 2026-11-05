"""Clearer CLI outcomes (P9, P10, P16 in docs/engine-cli-review.md)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.cli.cycle_hint import unknown_cycle_hint
from qasight.cli.server_url import server_url_problem
from qasight.services.flaky_service import FlakyReport

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


# --- P9: an empty shard ------------------------------------------------------------


def _tests_file(tmp_path: Path, *names: str) -> str:
    path = tmp_path / "tests.txt"
    path.write_text("\n".join(names) + "\n", encoding="utf-8")
    return str(path)


def test_an_empty_shard_exits_1_instead_of_printing_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("qasight.services.test_split.load_test_times", lambda *a, **k: [])
    tests = _tests_file(tmp_path, "tests/test_a.py::test_one")
    args = ["split", "--cycle", "web", "--shards", "3", "--index", "3", "--tests", tests]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "nothing to run on shard 3 of 3" in result.output

    allowed = CliRunner().invoke(app, [*args, "--allow-empty"])
    assert allowed.exit_code == 0
    assert "tests/test_a.py" not in allowed.stdout


def test_a_shard_with_tests_still_prints_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("qasight.services.test_split.load_test_times", lambda *a, **k: [])
    tests = _tests_file(tmp_path, "tests/test_a.py::test_one")
    result = CliRunner().invoke(
        app, ["split", "--cycle", "web", "--shards", "1", "--index", "1", "--tests", tests]
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "tests/test_a.py::test_one"


# --- P10: a cycle name with no runs ----------------------------------------------


def test_the_hint_names_the_cycles_that_have_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qasight.history.read_model.latest_run_per_cycle", lambda: {"web": 1, "api": 2}
    )
    hint = unknown_cycle_hint("wbe")
    assert hint is not None
    assert "cycles with runs: api, web" in hint and "did you mean 'web'" in hint
    assert unknown_cycle_hint("web") is None


def test_flaky_on_an_unknown_cycle_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qasight.services.flaky_service.find_flaky_tests",
        lambda *, cycle, window: FlakyReport(
            cycle=cycle, window=window, runs_analyzed=0, tests_analyzed=0, flaky=()
        ),
    )
    monkeypatch.setattr("qasight.history.read_model.latest_run_per_cycle", lambda: {"web": 1})
    result = CliRunner().invoke(app, ["flaky", "--cycle", "wbe"])
    assert result.exit_code == 2
    assert "did you mean 'web'" in result.output


def test_badge_on_an_unknown_cycle_warns_on_stderr_and_still_exits_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qasight.services.badge import GREY, Badge

    monkeypatch.setattr(
        "qasight.services.badge.badge_for_cycle",
        lambda cycle, *, label: Badge(label, "no runs", GREY),
    )
    monkeypatch.setattr("qasight.history.read_model.latest_run_per_cycle", lambda: {"web": 1})
    result = CliRunner().invoke(app, ["badge", "--cycle", "wbe", "--format", "json"])
    assert result.exit_code == 0
    assert result.stdout.lstrip().startswith("{")
    assert "did you mean 'web'" in result.stderr


# --- P16: a server URL without a scheme -------------------------------------------


@pytest.mark.parametrize("url", ["not-a-url", "qasight.example.com", "ftp://x", "http://"])
def test_a_bad_server_url_is_named(url: str) -> None:
    problem = server_url_problem(url)
    assert problem is not None and "--server must be a URL" in problem


@pytest.mark.parametrize("url", [None, "", "http://localhost:8000", "https://qasight.example.com/"])
def test_good_server_urls_pass(url: str | None) -> None:
    assert server_url_problem(url) is None


@pytest.mark.parametrize(
    "args",
    [
        ["ingest", "r.xml", "--cycle", "c", "--server", "not-a-url"],
        ["split", "--cycle", "c", "--shards", "2", "--server", "not-a-url"],
        ["lease", "list", "--server", "not-a-url"],
    ],
)
def test_commands_exit_2_on_a_bad_server_url(args: list[str]) -> None:
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2, result.output
    assert "--server must be a URL" in result.output
