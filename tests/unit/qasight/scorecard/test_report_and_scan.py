"""Scorecard store, roll-up, version comparison, gate and Markdown; and scans of folders and git URLs."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from qasight.repository.db_config import reset_engine_cache
from qasight.services.scorecard import scan as scan_mod
from qasight.services.scorecard.parsers import ToolResult
from qasight.services.scorecard.report import (
    build_scorecard,
    gate_failures,
    overall_score,
    projects,
    to_markdown,
)
from qasight.services.scorecard.scan import (
    ScanError,
    detect_languages,
    is_git_url,
    plan_tools,
    resolve_target,
    run_scan,
)
from qasight.services.scorecard.store import ScorecardError, ScorecardStore

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ScorecardStore]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    reset_engine_cache()
    yield ScorecardStore()
    reset_engine_cache()


def _record(
    store: ScorecardStore, version: str, tool: str, score: int, project: str = "app"
) -> None:
    store.record(
        project=project,
        version=version,
        tool=tool,
        result=ToolResult(score=score, summary=f"{tool} {score}"),
    )


def test_overall_is_the_mean_of_category_means(store: ScorecardStore) -> None:
    _record(store, "1.0", "bandit", 40)  # security
    _record(store, "1.0", "semgrep", 80)  # security -> 60
    _record(store, "1.0", "ruff", 100)  # code -> 100
    card = build_scorecard("app", "1.0", store=store)
    assert card is not None
    assert {c.key: c.score for c in card.categories} == {"security": 60, "code": 100}
    assert (card.score, card.grade) == (80, "B")


def test_half_rounds_up_like_the_ui(store: ScorecardStore) -> None:
    enabled = store.enabled()
    _record(store, "1.0", "pip-audit", 100)
    _record(store, "1.0", "zizmor", 37)
    score, categories = overall_score(store.results("app"), enabled)
    assert categories["supply-chain"] == 69
    assert score == 69


def test_switched_off_tools_are_shown_but_not_counted(store: ScorecardStore) -> None:
    _record(store, "1.0", "bandit", 20)
    _record(store, "1.0", "ruff", 100)
    store.set_enabled("bandit", False)
    card = build_scorecard("app", "1.0", store=store)
    assert card is not None and card.score == 100
    assert [t.tool for t in card.tools if not t.enabled] == ["bandit"]
    store.reset_enabled()
    assert store.enabled()["bandit"] is True


def test_compares_with_the_previous_version_by_number(store: ScorecardStore) -> None:
    _record(store, "1.10.0", "bandit", 50)
    _record(store, "1.9.0", "bandit", 90)
    _record(store, "1.2.0", "bandit", 70)
    card = build_scorecard("app", store=store)
    assert card is not None
    assert card.version == "1.10.0"
    assert card.previous_version == "1.9.0"
    assert card.tools[0].previous_score == 90
    assert [v.version for v in card.versions] == ["1.10.0", "1.9.0", "1.2.0"]
    assert gate_failures(card, min_score=60) == ["overall score 50 is below 60"]
    assert gate_failures(card, no_drop=True) == ["Bandit dropped from 90 to 50 since 1.9.0"]
    assert gate_failures(card, min_score=40, min_tool=40) == []


def test_versions_that_are_not_numbers_go_by_time(store: ScorecardStore) -> None:
    _record(store, "abc123", "ruff", 70)
    _record(store, "main", "ruff", 90)
    card = build_scorecard("app", store=store)
    assert card is not None and (card.version, card.previous_version) == ("main", "abc123")


def test_newer_report_replaces_the_older_one(store: ScorecardStore) -> None:
    _record(store, "1.0", "ruff", 10)
    _record(store, "1.0", "ruff", 95)
    assert [r.score for r in store.results("app", "1.0")] == [95]


def test_projects_and_empty(store: ScorecardStore) -> None:
    assert build_scorecard(store=store) is None
    _record(store, "1.0", "ruff", 90, project="a")
    _record(store, "2.0", "ruff", 90, project="b")
    assert [p.project for p in projects(store)] == ["b", "a"]
    assert build_scorecard("a", "9.9", store=store) is None


def test_bad_names_and_scores(store: ScorecardStore) -> None:
    with pytest.raises(ScorecardError, match="unknown tool"):
        _record(store, "1.0", "nope", 50)
    with pytest.raises(ScorecardError, match="project and a version"):
        _record(store, " ", "ruff", 50)
    with pytest.raises(ScorecardError, match="0 to 100"):
        store.record_raw(project="a", version="1", tool="ruff", score=101, summary="")


def test_markdown_lists_categories_tools_and_fixes(store: ScorecardStore) -> None:
    store.record_raw(project="app", version="1.0", tool="bandit", score=90, summary="ok")
    store.record_raw(
        project="app",
        version="1.1",
        tool="bandit",
        score=60,
        summary="1 issue",
        findings=[{"severity": "high", "title": "B602", "detail": "shell", "location": "a.py:1"}],
    )
    card = build_scorecard("app", "1.1", store=store)
    assert card is not None
    text = to_markdown(card, skipped={"zap": "needs a running app (--url)"})
    assert "# Release scorecard: app 1.1" in text
    assert "**Overall: 60/100 (D)**, -30 since 1.0." in text
    assert "| Bandit | 60 (-30) | 1 issue |" in text
    assert "**high** · Bandit: B602 `a.py:1`. shell" in text
    assert "- OWASP ZAP: needs a running app (--url)" in text


# --- scans --------------------------------------------------------------------


def test_git_url_forms() -> None:
    assert is_git_url("https://github.com/torvalds/linux")
    assert is_git_url("git@github.com:torvalds/linux.git")
    assert is_git_url("github.com/torvalds/linux")
    assert not is_git_url("./linux")
    assert scan_mod._owner_repo("https://github.com/torvalds/linux.git") == (
        "torvalds/linux",
        "github.com",
    )
    assert scan_mod._owner_repo("git@gitlab.com:group/sub/proj.git") == ("sub/proj", "gitlab.com")


def test_resolve_a_local_git_folder(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["commit", "-qm", "x"],
        ["tag", "v2.0.0"],
        ["remote", "add", "origin", "https://github.com/acme/proj.git"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)
    target = resolve_target(str(repo))
    assert (target.project, target.version, target.github_repo) == (
        "acme/proj",
        "v2.0.0",
        "acme/proj",
    )
    assert target.commit_sha and len(target.commit_sha) == 40
    assert resolve_target(str(repo), project="p", version="9").version == "9"


def test_resolve_rejects_bad_targets(tmp_path: Path) -> None:
    with pytest.raises(ScanError, match="not a folder"):
        resolve_target(str(tmp_path / "missing"))
    with pytest.raises(ScanError):
        resolve_target("--upload-pack=evil")


def test_detect_languages(tmp_path: Path) -> None:
    (tmp_path / "kernel").mkdir()
    (tmp_path / "kernel" / "sched.c").write_text("", encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "a.py").write_text("", encoding="utf-8")
    assert detect_languages(tmp_path) == {"c", "node", "github-actions"}


def test_plan_tools_says_why_each_tool_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: "/bin/x" if name in {"bandit", "lighthouse"} else None
    )
    enabled = {"ruff": False}
    run, skipped = plan_tools(languages={"python"}, enabled=enabled)
    assert [t.id for t in run] == ["bandit"]
    assert skipped["ruff"] == "switched off"
    assert skipped["semgrep"].startswith("not installed")
    assert skipped["flawfinder"] == "no c code found"
    assert skipped["lighthouse"] == "needs a running app (--url)"
    run, _ = plan_tools(languages={"python"}, enabled=enabled, url="http://localhost:1")
    assert "lighthouse" in [t.id for t in run]
    run, skipped = plan_tools(languages={"python"}, enabled=enabled, only=["ruff", "coverage"])
    assert run == [] and skipped["coverage"].startswith("import only")


def _fake_tool(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_run_scan_records_each_tool_and_survives_a_broken_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # ruff prints its report; bandit exits 1 (findings) after writing it; flawfinder writes nothing.
    _fake_tool(
        bin_dir,
        "ruff",
        'echo \'[{"code": "F401", "message": "unused", "filename": "a.py", "location": {"row": 1}}]\'',
    )
    bandit_report = json.dumps({"results": []}).replace('"', '\\"')
    _fake_tool(
        bin_dir,
        "bandit",
        f'while [ "$1" != "-o" ]; do shift; done; echo "{bandit_report}" > "$2"; exit 1',
    )
    _fake_tool(bin_dir, "flawfinder", "echo boom >&2; exit 2")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("import os\n", encoding="utf-8")
    (repo / "b.c").write_text("", encoding="utf-8")
    target = resolve_target(str(repo), project="p", version="1")
    recorded: dict[str, int] = {}
    events: list[dict] = []
    outcome = run_scan(
        target,
        out_dir=tmp_path / "out",
        enabled={},
        record=lambda tool, result: recorded.__setitem__(tool, result.score),
        only=["ruff", "bandit", "flawfinder"],
        on_event=events.append,
    )
    assert recorded == {"bandit": 100, "ruff": 96}
    assert "flawfinder" in outcome.errors and "boom" in outcome.errors["flawfinder"]
    assert (tmp_path / "out" / "ruff" / "report.json").is_file()
    assert (
        (tmp_path / "out" / "bandit" / "bandit.log")
        .read_text(encoding="utf-8")
        .startswith("$ bandit")
    )
    assert [e["status"] for e in events if e["type"] == "tool" and e["tool"] == "ruff"] == [
        "running",
        "done",
    ]
