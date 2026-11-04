"""``qasight init --pr`` (qasight/services/setup_pr.py): branch, commit, push, pull request."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.services import setup_pr

_JUNIT = (
    '<?xml version="1.0"?><testsuite name="s" tests="1">'
    '<testcase classname="t" name="test_ok" time="0.1"/></testsuite>\n'
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout with one commit on ``main``, an ``origin`` remote and some results."""
    for key in ("TESTO_HOME", "CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }.items():
        monkeypatch.setenv(key, value)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    root = tmp_path / "shop"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "requirements.txt").write_text("pytest\n")
    _git(root, "add", "requirements.txt")
    _git(root, "commit", "-q", "-m", "first")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "-u", "origin", "main")
    (root / "reports").mkdir()
    (root / "reports" / "junit.xml").write_text(_JUNIT)
    (root / "unrelated.txt").write_text("staged, not ours\n")
    _git(root, "add", "unrelated.txt")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.chdir(tmp_path)
    return root


def _fake_gh(bin_dir: Path, url: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" > "{bin_dir}/gh-args"\necho {url}\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)


def test_pr_commits_only_the_written_files_pushes_and_opens_the_pull_request(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    _fake_gh(bin_dir, "https://github.com/o/shop/pull/7")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = CliRunner().invoke(app, ["init", "--dir", str(repo), "--pr"])

    assert result.exit_code == 0, result.output
    assert "Opened https://github.com/o/shop/pull/7" in result.output
    assert _git(repo, "branch", "--show-current") == setup_pr.SETUP_BRANCH
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(committed) == [
        ".github/workflows/qasight.yml",
        ".gitignore",
        "qasight.yaml",
    ]
    assert "unrelated.txt" in _git(repo, "diff", "--cached", "--name-only")  # still staged
    assert _git(repo, "ls-remote", "origin", setup_pr.SETUP_BRANCH)
    gh_args = (bin_dir / "gh-args").read_text()
    assert "pr create --base main --head qasight-setup" in gh_args


def test_pr_without_gh_pushes_and_prints_the_last_step(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_pr.shutil, "which", lambda _name: None)

    result = CliRunner().invoke(app, ["init", "--dir", str(repo), "--pr"])

    assert result.exit_code == 0, result.output
    assert "GitHub CLI" in result.output
    assert "gh pr create --base main --head qasight-setup" in result.output
    assert _git(repo, "ls-remote", "origin", setup_pr.SETUP_BRANCH)


def test_a_taken_branch_name_gets_a_suffix(repo: Path) -> None:
    _git(repo, "branch", setup_pr.SETUP_BRANCH)

    assert setup_pr._free_branch(repo, setup_pr.SETUP_BRANCH) == "qasight-setup-2"


def test_not_a_git_repository(tmp_path: Path) -> None:
    result = setup_pr.open_setup_pr(tmp_path, files=["a"], base="main", cycle="c")

    assert result.url is None and "not a git repository" in str(result.error)


def test_dependabot_is_written_only_when_asked_and_missing(repo: Path) -> None:
    CliRunner().invoke(app, ["init", "--dir", str(repo), "--no-run", "--dependabot"])

    config = yaml.safe_load((repo / ".github" / "dependabot.yml").read_text())
    assert config["updates"][0]["package-ecosystem"] == "github-actions"


def test_server_writes_upload_inputs_and_stores_the_token_secret(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" > "{bin_dir}/gh-args"\ncat > "{bin_dir}/gh-stdin"\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TESTO_API_TOKEN", "tok-123")

    result = CliRunner().invoke(
        app, ["init", "--dir", str(repo), "--no-run", "--server", "https://testo.example.com/"]
    )

    assert result.exit_code == 0, result.output
    step = yaml.safe_load((repo / ".github/workflows/qasight.yml").read_text())["jobs"]["tests"][
        "steps"
    ][-1]
    assert step["with"]["server-url"] == "https://testo.example.com"
    assert step["with"]["server-token"] == "${{ secrets.TESTO_API_TOKEN }}"
    assert (bin_dir / "gh-args").read_text().strip() == "secret set TESTO_API_TOKEN"
    assert (bin_dir / "gh-stdin").read_text() == "tok-123"
    assert "tok-123" not in result.output


def test_server_without_a_token_says_which_secret_to_add(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TESTO_API_TOKEN", raising=False)

    result = CliRunner().invoke(
        app, ["init", "--dir", str(repo), "--no-run", "--server", "https://testo.example.com"]
    )

    assert result.exit_code == 0, result.output
    assert "TESTO_API_TOKEN secret" in result.output


def test_server_must_be_a_url(repo: Path) -> None:
    result = CliRunner().invoke(app, ["init", "--dir", str(repo), "--server", "testo.example"])

    assert result.exit_code == 2


def test_forks_leaves_the_comment_as_an_artifact_and_adds_the_posting_workflow(
    repo: Path,
) -> None:
    result = CliRunner().invoke(app, ["init", "--dir", str(repo), "--no-run", "--forks"])

    assert result.exit_code == 0, result.output
    tests = yaml.safe_load((repo / ".github/workflows/qasight.yml").read_text())
    assert tests["permissions"] == {"contents": "read"}
    assert tests["jobs"]["tests"]["steps"][-1]["with"]["comment-artifact"] == "true"
    poster = yaml.safe_load((repo / ".github/workflows/qasight-comment.yml").read_text())
    # PyYAML reads the bare `on:` key as True.
    trigger = poster[True]["workflow_run"]
    assert trigger["workflows"] == [tests["name"]]
    assert poster["permissions"] == {"actions": "read", "pull-requests": "write"}
    steps = poster["jobs"]["comment"]["steps"]
    assert not any("checkout" in str(s.get("uses")) for s in steps)
    assert steps[-1]["with"] == {"post-comments-from": "testo-comment"}
