"""``qasight try`` — the pull request comment your latest results would get, recording nothing.

The command-line partner of the ``/try`` page: it takes the result files a
test run left (found in the project like ``qasight ingest`` finds them, given as
arguments, or downloaded from the latest GitHub Actions run with
``--from-github``), records them as a run in a **throwaway copy** of the
history, and prints what ``qasight report markdown`` (the job summary and pull
request comment) would say about them.

Nothing touches the real history: the run goes into a new temporary folder
with its own SQLite file and ``TESTO_HOME``, like ``qasight demo``. When the
history is a local SQLite file, it is copied in first, so the preview compares
with the runs you already have (for a pull request, the latest run on the
default branch). No config is read, so no reporter or alert fires.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode


def _sqlite_file(url: str) -> Path | None:
    """The file a ``sqlite:///...`` URL names, if it is one that exists."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    path = Path(url[len(prefix) :].split("?", 1)[0]).expanduser()
    return path if path.is_file() else None


def _try_env(root: Path, *, source_db: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    db = root / "testo_history.db"
    if source_db is not None:
        shutil.copy2(source_db, db)
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env["TESTO_HOME"] = str(root / ".testo")
    # A preview is not a CI run, and must not look like one to the history.
    for key in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "TESTO_SERVER_URL"):
        env.pop(key, None)
    return env


def _testo(args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-c", "from qasight.cli.app import main; main()", *args]
    return subprocess.run(  # noqa: S603 — our own CLI, fixed argv
        cmd, cwd=cwd, env=env, check=False, capture_output=True, text=True
    )


def _download_github_results(repo: Path, run_id: str | None, dest: Path) -> str:
    """Download a workflow run's artifacts with ``gh``; returns the run id."""
    if shutil.which("gh") is None:
        raise typer.BadParameter(
            "needs the GitHub CLI (https://cli.github.com), signed in with `gh auth login`",
            param_hint="--from-github",
        )
    if not run_id:
        proc = subprocess.run(  # noqa: S603
            ["gh", "run", "list", "--limit", "1", "--status", "completed", "--json", "databaseId"],  # noqa: S607
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        runs = json.loads(proc.stdout or "[]") if proc.returncode == 0 else []
        if not runs:
            raise typer.BadParameter(
                f"no finished workflow run found ({proc.stderr.strip() or 'gh run list was empty'})",
                param_hint="--from-github",
            )
        run_id = str(runs[0]["databaseId"])
    proc = subprocess.run(  # noqa: S603
        ["gh", "run", "download", run_id, "--dir", str(dest)],  # noqa: S607
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise typer.BadParameter(
            f"could not download run {run_id}'s artifacts: {proc.stderr.strip()}. "
            "Upload the result files as an artifact (actions/upload-artifact) to preview them.",
            param_hint="--from-github",
        )
    return run_id


def _run_project_tests(project: Path) -> None:
    """Run each detected stack's tests the way the generated CI file does, so they leave results."""
    from qasight.services.ci_templates import ci_test_command
    from qasight.services.project_setup import detect_project

    facts = detect_project(project)
    if not facts.stacks:
        raise typer.BadParameter(
            "no test runner found to run (pytest, Jest, Vitest, Playwright, Go, .NET, Maven, "
            "Gradle, RSpec, PHPUnit); run your tests yourself, then `qasight try`",
            param_hint="--run",
        )
    for stack in facts.stacks:
        command = ci_test_command(stack)
        typer.echo(f"Running {stack.label}: {command}", err=True)
        # Failing tests are what a preview is for; the exit code is not. Their output
        # goes to stderr so stdout stays the comment.
        proc = subprocess.run(  # noqa: S602 — the project's own test command
            command,
            shell=True,
            cwd=project,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        typer.echo(proc.stdout.rstrip(), err=True)


def try_comment(
    reports: list[str] = typer.Argument(
        None,
        help="Result files, folders or globs (default: the ones a test run left in --repo, "
        "found by content).",
    ),
    repo: Path = typer.Option(
        Path("."), "--repo", file_okay=False, help="The project (default: here)."
    ),
    cycle: str = typer.Option(
        None, "--cycle", help="Cycle to compare with (default: the project folder's name)."
    ),
    from_github: bool = typer.Option(
        False,
        "--from-github",
        help="Use the result files uploaded as artifacts by the latest finished GitHub Actions "
        "run (needs the gh CLI).",
    ),
    run_id: str = typer.Option(
        None, "--run-id", help="With --from-github: this workflow run instead of the latest."
    ),
    baseline_branch: str = typer.Option(
        None,
        "--baseline-branch",
        help="Compare with this branch's latest run (default: the repository's default branch, "
        "as a pull request would).",
    ),
    out: Path = typer.Option(
        None, "--out", "-o", help="Write the comment to this file instead of printing it."
    ),
    run_tests: bool = typer.Option(
        False,
        "--run",
        help="Run the project's tests first (the command `qasight init` would put in CI, with "
        "its result-file flag), then preview their results.",
    ),
) -> None:
    """Show the pull request comment your latest results would get. Your history is not changed."""
    from qasight.repository.db_config import resolve_database_url
    from qasight.services.ci_templates import default_branch
    from qasight.services.project_setup import (
        cycle_name_for,
        find_result_files,
        result_patterns,
    )

    project = repo.expanduser().resolve()
    cycle_name = (cycle or "").strip() or cycle_name_for(project)
    if run_tests:
        if from_github or reports:
            raise typer.BadParameter(
                "can't be combined with --from-github or result files", param_hint="--run"
            )
        _run_project_tests(project)
    root = Path(tempfile.mkdtemp(prefix="testo-try-"))
    try:
        results_root = project
        if from_github:
            results_root = root / "ci-results"
            used = _download_github_results(project, run_id, results_root)
            typer.echo(f"Downloaded the artifacts of workflow run {used}", err=True)
        patterns = [str(Path(r).expanduser().resolve()) for r in reports or []]
        if not patterns:
            found = find_result_files(results_root, include_tracked=from_github)
            if not found:
                typer.echo(
                    f"No result files found in {results_root}. Run your tests so they write one "
                    "(JUnit XML, Jest --json, go test -json, ...), or pass the files.",
                    err=True,
                )
                raise typer.Exit(int(EngineExitCode.INVALID_INPUT))
            patterns = [str(results_root / p) for p in result_patterns(found, repo=results_root)]
        typer.echo("Previewing: " + ", ".join(Path(p).name for p in patterns), err=True)

        with contextlib.chdir(project):
            source_db = _sqlite_file(resolve_database_url())
        env = _try_env(root, source_db=source_db)
        ingest = _testo(
            ["ingest", *patterns, "--cycle", cycle_name, "--repo", str(results_root), "--ci"],
            cwd=root,
            env=env,
        )
        # 1 = some tests failed, which is a fine thing to preview.
        if ingest.returncode not in (0, int(EngineExitCode.DOMAIN_FAILURE)):
            typer.echo((ingest.stdout + ingest.stderr).strip(), err=True)
            raise typer.Exit(ingest.returncode)

        branch = (baseline_branch or "").strip() or default_branch(project)
        markdown = _testo(
            ["report", "markdown", "--cycle", cycle_name, "--baseline-branch", branch],
            cwd=root,
            env=env,
        )
        if markdown.returncode != 0:
            typer.echo(markdown.stderr.strip(), err=True)
            raise typer.Exit(markdown.returncode)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown.stdout, encoding="utf-8")
        typer.echo(f"Wrote the preview to {out}", err=True)
    else:
        typer.echo(markdown.stdout, nl=False)
    note = (
        "compared with your local history"
        if source_db
        else "no local history to compare with, so this is the first run's comment"
    )
    typer.echo(f"\n(Preview only: {note}. Nothing was recorded.)", err=True)
