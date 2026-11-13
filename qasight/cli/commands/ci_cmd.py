"""``qasight ci publish`` — after a CI run: summary, review comment, annotations, chat alerts.

The step every CI system runs after its tests are recorded (``qasight run`` or
``qasight ingest``). It works out where it runs (GitHub Actions, GitLab CI, or
anything else) and does what fits; see :mod:`qasight.services.ci_publish`.
"""

from __future__ import annotations

from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(
    help="CI helpers: publish a recorded run where the CI shows it.",
    no_args_is_help=True,
)


@app.command("publish")
def publish(
    cycle: list[str] = typer.Option(
        [],
        "--cycle",
        help="Cycle whose latest run to publish; repeat for several (default: the latest run "
        "of any cycle, without annotations or alerts).",
    ),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Also write the Markdown summary to this file."
    ),
    comment: bool = typer.Option(
        True,
        "--comment/--no-comment",
        help="Comment on the pull request (GitHub: $GITHUB_TOKEN) or merge request "
        "(GitLab: $TESTO_GITLAB_TOKEN), editing the earlier comment of the cycle.",
    ),
    job_summary: bool = typer.Option(
        True, "--job-summary/--no-job-summary", help="GitHub: append to the job summary."
    ),
    annotations: bool = typer.Option(
        True, "--annotations/--no-annotations", help="GitHub: failing tests on the diff."
    ),
    notify_when: str = typer.Option(
        "failure",
        "--notify-when",
        help="Chat alert to each set webhook ($TESTO_SLACK_WEBHOOK_URL, "
        "$TESTO_TEAMS_WEBHOOK_URL): failure, change, always, or never.",
    ),
    ai: bool = typer.Option(False, "--ai", help="Add the AI summary to a failed run."),
    comment_dir: Path | None = typer.Option(
        None,
        "--comment-dir",
        help="GitHub, pull requests from forks: write the comment here for a trusted "
        "workflow to post, instead of posting it.",
    ),
    workspace: Path = typer.Option(
        Path("."), "--workspace", help="Repository root, for annotation paths."
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c", help="qasight.yaml whose stage folders help find failing files."
    ),
) -> None:
    """Publish each cycle's latest recorded run. Never fails the job for a publishing error."""
    import os

    from qasight.services.ci_publish import PublishOptions
    from qasight.services.ci_publish import publish as publish_run

    when = notify_when.strip().lower()
    if when not in {"failure", "change", "always", "never"}:
        typer.echo("--notify-when takes failure, change, always or never", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    found = 0
    names: list[str | None] = list(dict.fromkeys(c.strip() for c in cycle if c.strip())) or [None]
    for name in names:
        opts = PublishOptions(
            job_summary=job_summary,
            comment=comment,
            annotations=annotations,
            notify_when=None if when == "never" else when,
            ai=ai,
            # Several cycles: one file each, next to the given name.
            out=out if out is None or len(names) == 1 else out.with_stem(f"{out.stem}-{name}"),
            comment_dir=comment_dir,
            workspace=workspace,
            config_path=config,
        )
        found += publish_run(
            cycle=name,
            env=dict(os.environ),
            opts=opts,
            echo=typer.echo,
            warn=lambda m: typer.echo(f"warning: {m}", err=True),
        )
    raise typer.Exit(code=0 if found else int(EngineExitCode.INVALID_INPUT))
