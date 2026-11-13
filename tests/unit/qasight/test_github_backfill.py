"""``qasight ingest --from-github``: past CI runs' result artifacts recorded as history."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.services import github_backfill

_JUNIT = """<?xml version="1.0"?>
<testsuite name="s" tests="2" failures="{failures}">
  <testcase classname="t" name="a" time="0.1"/>
  <testcase classname="t" name="b" time="0.2">{failure}</testcase>
</testsuite>
"""

_FAKE_GH = """\
import json, os, shutil, sys
from pathlib import Path

state = Path(os.environ["FAKE_GH_STATE"])
args = sys.argv[1:]
with open(state / "calls.log", "a") as log:
    log.write(" ".join(args) + "\\n")
if args[:2] == ["run", "list"]:
    print((state / "runs.json").read_text())
elif args[:2] == ["repo", "view"]:
    print("acme/shop")
elif args[:2] == ["run", "download"]:
    run_id, dest = args[2], Path(args[args.index("--dir") + 1])
    source = state / "artifacts" / run_id
    if not source.is_dir():
        print("no valid artifacts found to download", file=sys.stderr)
        sys.exit(1)
    shutil.copytree(source, dest, dirs_exist_ok=True)
else:
    sys.exit(3)
"""


def _run(run_id: int, day: int) -> dict[str, object]:
    return {
        "databaseId": run_id,
        "headBranch": "main",
        "headSha": f"sha{run_id}",
        "createdAt": f"2026-10-{day:02d}T08:00:00Z",
        "updatedAt": f"2026-10-{day:02d}T08:05:00Z",
        "workflowName": "CI",
        "event": "push",
    }


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "gh-state"
    (state / "bin").mkdir(parents=True)
    script = state / "bin" / "gh"
    script.write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    (state / "runs.json").write_text(
        json.dumps([_run(30, 3), _run(10, 1), _run(20, 2)]), encoding="utf-8"
    )
    for run_id, failures in ((10, 0), (30, 1)):
        folder = state / "artifacts" / str(run_id) / "test-results"
        folder.mkdir(parents=True)
        failure = '<failure message="boom"/>' if failures else ""
        (folder / "TEST-s.xml").write_text(
            _JUNIT.format(failures=failures, failure=failure), encoding="utf-8"
        )
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    monkeypatch.setenv("PATH", f"{state / 'bin'}{os.pathsep}{os.environ['PATH']}")
    for key in ("GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_REF_NAME"):
        monkeypatch.delenv(key, raising=False)
    repo = tmp_path / "shop"
    repo.mkdir()
    monkeypatch.chdir(repo)
    return state


def _history(cycle: str) -> list[object]:
    from qasight.repository.db import get_repository

    return sorted(get_repository().list_runs(cycle=cycle, limit=50), key=lambda r: r.start_time)


def test_list_runs_reads_gh_json_oldest_first(fake_gh: Path) -> None:
    runs = github_backfill.list_runs(cwd=Path.cwd(), limit=5, workflow="ci.yml", branch="main")
    assert [r.run_id for r in runs] == ["10", "20", "30"]
    assert runs[0].sha == "sha10" and runs[0].branch == "main"
    call = (fake_gh / "calls.log").read_text().splitlines()[0]
    assert "--limit 5" in call and "--workflow ci.yml" in call and "--branch main" in call


def test_from_github_records_each_run_dated_and_skips_the_rest(fake_gh: Path) -> None:
    result = CliRunner().invoke(app, ["ingest", "--from-github", "--cycle", "shop"])
    assert result.exit_code == 0, result.output
    assert "Recorded 2 past run(s) into cycle 'shop'" in result.output
    assert "1 without result files" in result.output  # run 20 uploaded nothing

    runs = _history("shop")
    assert [(r.metadata_ or {}).get("ci_pipeline_id") for r in runs] == ["10", "30"]
    first = runs[0]
    assert first.start_time.replace(tzinfo=None).isoformat() == "2026-10-01T08:00:00"
    assert (first.metadata_ or {}).get("backfilled_from") == "github"
    assert (first.metadata_ or {}).get("ci_commit_sha") == "sha10"
    assert (first.metadata_ or {}).get("ci_ref_name") == "main"
    assert "GITHUB_RUN_ID" not in os.environ  # the run's CI facts don't leak out

    again = CliRunner().invoke(app, ["ingest", "--from-github", "--cycle", "shop"])
    assert again.exit_code == 0, again.output
    assert "Recorded 0 past run(s)" in again.output
    assert "2 already recorded" in again.output
    assert len(_history("shop")) == 2


def test_from_github_without_gh_or_with_server_is_invalid_input(
    fake_gh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = CliRunner().invoke(
        app, ["ingest", "--from-github", "--server", "https://qa.example.com"]
    )
    assert server.exit_code == 2
    assert "drop --server" in server.output

    monkeypatch.setenv("PATH", str(fake_gh / "nothing-here"))
    missing = CliRunner().invoke(app, ["ingest", "--from-github"])
    assert missing.exit_code == 2
    assert "GitHub CLI" in missing.output
