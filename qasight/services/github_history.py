"""Download the run history the GitHub Action keeps, for ``qasight ui --from-github``.

With ``history: auto`` the Action keeps a SQLite history in the Actions cache,
which only workflows can read, and also uploads that file as a
``testo-history-<workflow>-<job>-<run>...`` artifact (``history-artifact``,
kept 7 days). This finds the newest such artifact through the GitHub CLI
(``gh api``, ``gh run download``), so sign-in and GitHub Enterprise hosts stay
``gh``'s job, and returns the downloaded database.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ARTIFACT_PREFIX = "testo-history-"
DB_NAME = "testo_history.db"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GithubHistoryError(RuntimeError):
    """No ``gh``, no artifact, or a download that failed; the message says which."""


@dataclass(frozen=True)
class HistoryArtifact:
    name: str
    run_id: str
    created_at: str


def _gh(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    if shutil.which("gh") is None:
        raise GithubHistoryError(
            "needs the GitHub CLI (https://cli.github.com), signed in with `gh auth login`"
        )
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["gh", *args],  # noqa: S607
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )


def find_artifact(*, cwd: Path, repo: str | None, run_id: str | None) -> HistoryArtifact:
    """The newest unexpired history artifact (of *run_id*, when given)."""
    if repo and not _REPO.match(repo):
        raise GithubHistoryError(f"--repo must look like owner/name, not {repo!r}")
    where = repo or "{owner}/{repo}"
    path = (
        f"repos/{where}/actions/runs/{run_id}/artifacts?per_page=100"
        if run_id
        else f"repos/{where}/actions/artifacts?per_page=100"
    )
    proc = _gh(["api", path], cwd=cwd)
    if proc.returncode != 0:
        raise GithubHistoryError(f"could not list the artifacts: {proc.stderr.strip()}")
    try:
        items = json.loads(proc.stdout or "{}").get("artifacts") or []
    except (ValueError, AttributeError) as exc:
        raise GithubHistoryError(f"unexpected answer from gh api: {exc}") from None
    found = sorted(
        (
            a
            for a in items
            if str(a.get("name", "")).startswith(ARTIFACT_PREFIX) and not a.get("expired")
        ),
        key=lambda a: str(a.get("created_at") or ""),
        reverse=True,
    )
    if not found:
        scope = f"run {run_id}" if run_id else "the newest 100 artifacts"
        raise GithubHistoryError(
            f"no testo-history artifact in {scope}. The Action uploads one when it keeps the "
            "history in the Actions cache (history: auto, history-artifact: true, from the "
            "release after 1.5.0); artifacts expire after 7 days"
        )
    newest = found[0]
    run = str(run_id or (newest.get("workflow_run") or {}).get("id") or "")
    if not run:
        raise GithubHistoryError(f"artifact {newest['name']} names no workflow run")
    return HistoryArtifact(
        name=str(newest["name"]), run_id=run, created_at=str(newest.get("created_at") or "")
    )


def download_history(
    *, cwd: Path, dest: Path, repo: str | None = None, run_id: str | None = None
) -> tuple[Path, HistoryArtifact]:
    """Download the newest history artifact into *dest*; returns the database file."""
    artifact = find_artifact(cwd=cwd, repo=repo, run_id=run_id)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    args = ["run", "download", artifact.run_id, "--name", artifact.name, "--dir", str(dest)]
    if repo:
        args += ["--repo", repo]
    proc = _gh(args, cwd=cwd)
    if proc.returncode != 0:
        raise GithubHistoryError(f"could not download {artifact.name}: {proc.stderr.strip()}")
    db = next(iter(sorted(dest.rglob(DB_NAME))), None)
    if db is None:
        raise GithubHistoryError(f"{artifact.name} has no {DB_NAME}")
    return db, artifact
