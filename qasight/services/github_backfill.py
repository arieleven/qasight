"""Fill the run history from CI runs that happened before QASight (``qasight ingest --from-github``).

Flaky detection, "got slower" and the trends need runs to compare; a new user
has none. Many workflows already upload their test results as artifacts
(JUnit XML from Surefire, Jest JSON, ``.trx`` ...). This lists a repository's
recent completed workflow runs with the GitHub CLI, downloads each run's
artifacts, finds the result files in them by content (as ``qasight ingest``
does with no arguments) and records each run as one run of the cycle, with its
commit, branch and run id, and dated when it happened on CI.

``gh`` does the talking to GitHub, so sign-in, tokens and GitHub Enterprise
hosts are its job. A run without artifacts, or whose artifacts hold no result
files, is skipped and counted; one already recorded (same CI run id) is skipped,
so running it again only adds what is new.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from qasight.services.project_setup import find_result_files


class BackfillError(RuntimeError):
    """No ``gh``, or ``gh`` couldn't list the runs; the message says which."""


@dataclass(frozen=True)
class CiRun:
    run_id: str
    branch: str
    sha: str
    created_at: str
    updated_at: str
    workflow: str
    event: str


@dataclass
class BackfillReport:
    recorded: list[CiRun] = field(default_factory=list)
    already: list[CiRun] = field(default_factory=list)
    no_results: list[CiRun] = field(default_factory=list)
    failed: list[tuple[CiRun, str]] = field(default_factory=list)


def _gh(args: list[str], *, cwd: Path, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    if shutil.which("gh") is None:
        raise BackfillError(
            "needs the GitHub CLI (https://cli.github.com), signed in with `gh auth login`"
        )
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["gh", *args],  # noqa: S607
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def list_runs(
    *, cwd: Path, limit: int, workflow: str | None = None, branch: str | None = None
) -> list[CiRun]:
    """The *limit* newest completed workflow runs, oldest first."""
    args = [
        "run",
        "list",
        "--status",
        "completed",
        "--limit",
        str(limit),
        "--json",
        "databaseId,headBranch,headSha,createdAt,updatedAt,workflowName,event",
    ]
    if workflow:
        args += ["--workflow", workflow]
    if branch:
        args += ["--branch", branch]
    proc = _gh(args, cwd=cwd, timeout=120)
    if proc.returncode != 0:
        raise BackfillError(proc.stderr.strip() or "gh run list failed")
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise BackfillError(f"gh run list printed something that isn't JSON: {exc}") from exc
    runs = [
        CiRun(
            run_id=str(r.get("databaseId", "")),
            branch=str(r.get("headBranch") or ""),
            sha=str(r.get("headSha") or ""),
            created_at=str(r.get("createdAt") or ""),
            updated_at=str(r.get("updatedAt") or r.get("createdAt") or ""),
            workflow=str(r.get("workflowName") or ""),
            event=str(r.get("event") or ""),
        )
        for r in rows
        if isinstance(r, dict) and r.get("databaseId")
    ]
    return sorted(runs, key=lambda r: r.created_at)


def repository_slug(*, cwd: Path) -> str | None:
    proc = _gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=cwd)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def download(run: CiRun, *, cwd: Path, dest: Path) -> str | None:
    """Download *run*'s artifacts into *dest*; returns an error, or ``None``."""
    proc = _gh(["run", "download", run.run_id, "--dir", str(dest)], cwd=cwd)
    if proc.returncode != 0:
        return proc.stderr.strip() or "gh run download failed"
    return None


@contextlib.contextmanager
def as_ci_run(run: CiRun, *, repository: str | None) -> Iterator[None]:
    """Make the recorded run carry *run*'s CI facts (read from the environment on save)."""
    values = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": run.run_id,
        "GITHUB_SHA": run.sha,
        "GITHUB_REF_NAME": run.branch,
        "GITHUB_REPOSITORY": repository or "",
        "GITHUB_HEAD_REF": "",
        "GITHUB_BASE_REF": "",
        "GITHUB_JOB": "",
    }
    before = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        yield
    finally:
        for key, old in before.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _when(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def recorded_run_ids(cycle: str) -> set[str]:
    """CI run ids already in the history for *cycle*."""
    from qasight.repository.db import get_repository

    rows = get_repository().list_runs(cycle=cycle, limit=5000)
    return {
        str((r.metadata_ or {}).get("ci_pipeline_id"))
        for r in rows
        if (r.metadata_ or {}).get("ci_pipeline_id")
    }


def backdate(cycle: str, run: CiRun) -> None:
    """Date the newest run of *cycle* (the one just recorded) when *run* happened on CI."""
    from qasight.repository.db import get_repository

    repo = get_repository()
    rows = repo.list_runs(cycle=cycle, limit=1)
    start, end = _when(run.created_at), _when(run.updated_at)
    if not rows or start is None:
        return
    record = rows[0]
    if str((record.metadata_ or {}).get("ci_pipeline_id")) != run.run_id:
        return
    record.start_time = start
    record.end_time = end or start
    meta = dict(record.metadata_ or {})
    meta.update(
        {
            "started_at": start.timestamp(),
            "finished_at": (end or start).timestamp(),
            "started_at_iso": start.isoformat(),
            "backfilled_from": "github",
        }
    )
    record.metadata_ = meta
    repo.bulk_update([record])


def backfill(
    *,
    cwd: Path,
    cycle: str,
    limit: int,
    record: Callable[[list[str], Path], int],
    workflow: str | None = None,
    branch: str | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> BackfillReport:
    """Record up to *limit* past CI runs; *record(reports, folder)* records one (``qasight ingest``)."""
    report = BackfillReport()
    runs = list_runs(cwd=cwd, limit=limit, workflow=workflow, branch=branch)
    if not runs:
        return report
    known = recorded_run_ids(cycle)
    repository = repository_slug(cwd=cwd)
    for run in runs:
        if run.run_id in known:
            report.already.append(run)
            continue
        with tempfile.TemporaryDirectory(prefix="qasight-backfill-") as tmp:
            folder = Path(tmp)
            error = download(run, cwd=cwd, dest=folder)
            if error and "no valid artifacts" in error.lower():
                report.no_results.append(run)
                continue
            if error:
                report.failed.append((run, error))
                continue
            files = find_result_files(folder, include_tracked=True)
            if not files:
                report.no_results.append(run)
                continue
            reports = sorted({os.path.relpath(p, folder) for p in files})
            progress(
                f"run {run.run_id} ({run.branch}, {run.created_at[:10]}): {len(reports)} file(s)"
            )
            with as_ci_run(run, repository=repository):
                code = record(reports, folder)
            if code not in (0, 1):
                report.failed.append((run, f"recording exited {code}"))
                continue
            backdate(cycle, run)
            report.recorded.append(run)
    return report
