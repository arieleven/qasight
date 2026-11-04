"""A run rendered as Markdown for job summaries and PR comments."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight import paths
from qasight.cli.app import app
from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services import run_summary_markdown as md
from tests._history_fakes import cycle_runs_from


def _run(run_id: str, *, created_at: float, returncode: int = 0) -> CompletedRunView:
    return CompletedRunView(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at + 1,
        test_kind="cycle",
        returncode=returncode,
        wall_duration_ms=12300.0,
        metrics_duration_ms=None,
        total_tests=3,
        passed=2,
        failed=1,
        broken=0,
        skipped=0,
        avg_case_ms=None,
        health_pct=66.7,
        target_repo=None,
        snapshot_dir=f"static/history/{run_id}/artifacts",
        audit_json=None,
        cycle="unit",
        stage_health=[
            {
                "name": "unit",
                "framework": "pytest",
                "returncode": returncode,
                "duration_s": 1.5,
                "total_tests": 3,
                "passed": 2,
                "failed": 1,
                "broken": 0,
                "skipped": 0,
            }
        ],
    )


def _write(root: Path, run: CompletedRunView, statuses: dict[str, str]) -> None:
    folder = root / str(run.snapshot_dir) / "unit" / "allure-results" / "pytest"
    folder.mkdir(parents=True, exist_ok=True)
    for name, status in statuses.items():
        (folder / f"{name}-result.json").write_text(
            json.dumps({"historyId": name, "name": name, "status": status}), encoding="utf-8"
        )


@pytest.fixture
def two_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[CompletedRunView]:
    runs = [_run("new", created_at=2, returncode=1), _run("old", created_at=1)]  # newest first
    _write(tmp_path, runs[1], {"test_a": "passed", "test_b": "passed"})
    _write(tmp_path, runs[0], {"test_a": "failed", "test_b": "passed", "test_c": "passed"})
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(md, "list_recent_runs", lambda *, limit: runs)
    monkeypatch.setattr(md, "list_cycle_runs", cycle_runs_from(runs))
    monkeypatch.setattr(
        md, "get_run", lambda *, run_id: next(r for r in runs if r.run_id == run_id)
    )
    return runs


def test_latest_run_of_a_cycle_against_the_one_before(two_runs: list[CompletedRunView]) -> None:
    summary = md.load_run_summary(cycle="unit")
    assert summary is not None
    assert (summary.run.run_id, summary.previous and summary.previous.run_id) == ("new", "old")
    assert summary.failing == ("test_a",)
    assert [(c.kind, c.name) for c in summary.changes] == [
        ("regression", "test_a"),
        ("added", "test_c"),
    ]


def test_a_specific_run_finds_its_own_previous_run(two_runs: list[CompletedRunView]) -> None:
    summary = md.load_run_summary(run_id="old")
    assert summary is not None and summary.previous is None


def test_markdown_starts_with_the_comment_marker(two_runs: list[CompletedRunView]) -> None:
    summary = md.load_run_summary(cycle="unit")
    assert summary is not None
    text = md.render_markdown(summary, run_url="https://ci/run/1")

    assert text.splitlines()[0] == md.comment_marker("unit") == "<!-- testo-summary:unit -->"
    assert "### ❌ QASight: `unit` failed" in text
    assert "**2 passed · 1 failed · 0 skipped** in 12.3s · pass rate 66.7%" in text
    assert "| unit | pytest | ❌ fail | 3 | 2 | 1 | 0 | 1.5s |" in text
    assert "- `test_a`" in text
    assert "**Since the previous run:** 1 regression, 1 new test" in text
    assert "[workflow run](https://ci/run/1)" in text


def test_first_run_says_there_is_nothing_to_compare(two_runs: list[CompletedRunView]) -> None:
    summary = md.load_run_summary(run_id="old")
    assert summary is not None
    assert "First recorded run of this cycle" in md.render_markdown(summary)


def test_pipes_in_test_names_do_not_break_the_table(two_runs: list[CompletedRunView]) -> None:
    summary = md.load_run_summary(cycle="unit")
    assert summary is not None
    renamed = replace(summary.changes[0], name="test[a|b]")
    text = md.render_markdown(replace(summary, changes=(renamed,)))
    assert "`test[a\\|b]`" in text


def test_cli_prints_and_writes(two_runs: list[CompletedRunView], tmp_path: Path) -> None:
    runner = CliRunner()
    printed = runner.invoke(app, ["report", "markdown", "--cycle", "unit"])
    assert printed.exit_code == 0, printed.stdout
    assert printed.stdout.startswith("<!-- testo-summary:unit -->")

    out = tmp_path / "summary.md"
    written = runner.invoke(app, ["report", "markdown", "--cycle", "unit", "--out", str(out)])
    assert written.exit_code == 0
    assert out.read_text(encoding="utf-8") == printed.stdout


def test_cli_without_runs_is_invalid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(md, "list_recent_runs", lambda *, limit: [])
    monkeypatch.setattr(md, "list_cycle_runs", cycle_runs_from([]))
    assert CliRunner().invoke(app, ["report", "markdown", "--cycle", "nope"]).exit_code == 2


def test_quarantined_failures_are_labelled(two_runs: list[CompletedRunView]) -> None:
    summary = md.load_run_summary(cycle="unit")
    assert summary is not None
    stage = {**summary.run.stage_health[0], "returncode": 0, "quarantined": ["test_a"]}
    text = md.render_markdown(
        replace(summary, run=replace(summary.run, returncode=0, stage_health=[stage]))
    )
    assert "| unit | pytest | ✅ pass · 1 quarantined |" in text
    assert "- `test_a` _(quarantined)_" in text


def test_a_pull_request_is_compared_with_its_target_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pr = replace(_run("pr", created_at=3, returncode=1), branch="feature", base_branch="main")
    other_pr = replace(_run("other", created_at=2), branch="other-feature", base_branch="main")
    main = replace(_run("main", created_at=1), branch="main")
    runs = [pr, other_pr, main]
    _write(tmp_path, main, {"test_a": "passed"})
    _write(tmp_path, other_pr, {"test_a": "failed"})
    _write(tmp_path, pr, {"test_a": "failed"})
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(md, "list_recent_runs", lambda *, limit: runs)
    monkeypatch.setattr(md, "list_cycle_runs", cycle_runs_from(runs))

    summary = md.load_run_summary(cycle="unit")

    assert summary is not None and summary.previous is not None
    assert summary.previous.run_id == "main"
    assert [c.kind for c in summary.changes] == ["regression"]
    assert "**Since the last run on `main`:** 1 regression" in md.render_markdown(summary)


def test_baseline_branch_falls_back_to_the_previous_run(two_runs: list[CompletedRunView]) -> None:
    summary = md.load_run_summary(cycle="unit", baseline_branch="main")
    assert summary is not None and summary.previous is not None
    assert summary.previous.run_id == "old"
    assert summary.baseline_branch is None
    assert "Since the previous run" in md.render_markdown(summary)


def test_pick_baseline_ignores_its_own_branch() -> None:
    run = replace(_run("new", created_at=2), branch="main", base_branch=None)
    older = [replace(_run("old", created_at=1), branch="main")]
    assert md.pick_baseline(run, older, "main") == (older[0], None)


def test_stack_overflows_and_crashes_are_labelled(
    two_runs: list[CompletedRunView], tmp_path: Path
) -> None:
    folder = tmp_path / str(two_runs[0].snapshot_dir) / "unit" / "allure-results" / "pytest"
    (folder / "test_a-result.json").write_text(
        json.dumps(
            {
                "historyId": "test_a",
                "name": "test_a",
                "status": "failed",
                "statusDetails": {
                    "message": "RecursionError: maximum recursion depth exceeded",
                    "trace": "Traceback ...",
                },
            }
        ),
        encoding="utf-8",
    )
    summary = md.load_run_summary(cycle="unit")
    assert summary is not None
    assert summary.crashes == {"test_a": "stack_overflow"}
    stage = {**summary.run.stage_health[0], "crash_kind": "crash"}

    text = md.render_markdown(replace(summary, run=replace(summary.run, stage_health=[stage])))

    assert "| unit | pytest | ❌ fail · 💥 crashed |" in text
    assert "- `test_a` 💥 _stack overflow_" in text


# --- names from the code under test can't break out (bug hunt 2026-10-31) ---------------


def test_a_backtick_in_a_test_name_cannot_unlock_mentions_or_html(
    two_runs: list[CompletedRunView],
) -> None:
    summary = md.load_run_summary(cycle="unit")
    assert summary is not None
    evil = "test_w[q` @octocat <img src=x> `z]"
    text = md.render_markdown(
        replace(summary, failing=(evil,), changes=(replace(summary.changes[0], name=evil),))
    )
    # One code span with a longer fence: nothing inside it is rendered.
    assert f"- ``{evil}``" in text
    assert f"| ``{evil}`` |" in text
    for line in text.splitlines():
        outside_code = "".join(line.split("``")[::2])
        assert "@octocat" not in outside_code and "<img" not in outside_code


def test_pipes_are_only_escaped_inside_tables(two_runs: list[CompletedRunView]) -> None:
    summary = md.load_run_summary(cycle="unit")
    assert summary is not None
    text = md.render_markdown(replace(summary, failing=("test[a|b]",)))
    assert "- `test[a|b]`" in text


def test_stage_cells_are_plain_text(two_runs: list[CompletedRunView]) -> None:
    summary = md.load_run_summary(cycle="unit")
    assert summary is not None
    stage = {**summary.run.stage_health[0], "name": "a|b", "framework": "x|<b>@y"}
    text = md.render_markdown(replace(summary, run=replace(summary.run, stage_health=[stage])))
    assert "| a\\|b | x\\|&lt;b&gt;&#64;y |" in text


def test_comment_marker_cannot_end_the_html_comment() -> None:
    assert md.comment_marker("a-->b") == "<!-- testo-summary:a__b -->"
    assert md.comment_marker("smoke-tests") == "<!-- testo-summary:smoke-tests -->"


def test_a_push_to_main_is_not_compared_with_a_feature_branch_run() -> None:
    run = replace(_run("new", created_at=3), branch="main", base_branch=None)
    feature = replace(_run("feature", created_at=2), branch="feature/x", base_branch="main")
    main = replace(_run("main", created_at=1), branch="main")
    assert md.pick_baseline(run, [feature, main], None) == (main, None)


def test_a_pr_with_no_target_run_yet_is_not_compared_with_another_pr() -> None:
    run = replace(_run("pr", created_at=3), branch="feature/y", base_branch="main")
    other = replace(_run("other", created_at=2), branch="feature/x", base_branch="main")
    assert md.pick_baseline(run, [other], None) == (None, None)
    own = replace(_run("own", created_at=1), branch="feature/y", base_branch="main")
    assert md.pick_baseline(run, [other, own], None) == (own, None)


def test_previous_run_without_stored_results_is_not_called_unchanged(
    two_runs: list[CompletedRunView], tmp_path: Path
) -> None:
    import shutil

    shutil.rmtree(tmp_path / str(two_runs[1].snapshot_dir))
    summary = md.load_run_summary(cycle="unit")
    assert summary is not None and summary.previous is not None
    text = md.render_markdown(summary)
    assert "No test changed outcome" not in text
    assert "No test results are stored for the previous run" in text


def test_stage_change_note_when_the_suite_was_recorded_another_way() -> None:
    before = _run("r1", created_at=1.0)
    now = replace(
        _run("r2", created_at=2.0),
        stage_health=[{**before.stage_health[0], "name": "junit", "framework": "junit"}],
    )

    note = md.stage_change_note(now, before)

    assert note is not None and "unit → junit" in note
    assert md.stage_change_note(before, replace(before, run_id="r0")) is None
    assert md.stage_change_note(now, None) is None


def test_auto_quarantined_tests_are_listed() -> None:
    run = _run("r2", created_at=2.0, returncode=1)
    summary = md.RunSummary(
        run=run,
        previous=None,
        failing=("test_race",),
        changes=(),
        auto_quarantined=("test_race",),
    )

    text = md.render_markdown(summary)

    assert "🔇 **Quarantined automatically** (flaky, failed here): `test_race`" in text
    assert "🔇" not in md.render_markdown(replace(summary, auto_quarantined=()))
