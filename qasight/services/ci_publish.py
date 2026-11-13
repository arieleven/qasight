"""After a CI run: the summary, the pull/merge request comment, annotations and chat alerts.

One step for every CI system, so a GitLab, Jenkins or CircleCI job gets what the
GitHub Action gives (``qasight ci publish``; the Action calls the same code):

* **Summary.** The run as Markdown (:mod:`~qasight.services.run_summary_markdown`),
  with the AI summary of a failed run when asked. On GitHub it is appended to the
  job summary; everywhere it is also written to ``--out`` when given.
* **Comment.** One comment per cycle that later runs edit in place, found by the
  Markdown's first line (a hidden marker): on a GitHub pull request with
  ``TESTO_GITHUB_TOKEN`` or ``GITHUB_TOKEN`` (``pull-requests: write``), on a
  GitLab merge request with ``TESTO_GITLAB_TOKEN`` (a project or personal token
  with ``api`` scope; the job token can't write notes).
* **Annotations.** On GitHub, failing tests as ``::error`` lines on the diff.
* **Alerts.** Slack and Microsoft Teams, to each channel whose webhook
  (``TESTO_SLACK_WEBHOOK_URL``, ``TESTO_TEAMS_WEBHOOK_URL``) is set, when the run
  matches ``notify_when``. A channel ``qasight.yaml`` already has a ``slack`` or
  ``teams`` reporter for is skipped: that reporter alerted during the run, and the
  config is the one place to set it up (:func:`configured_alert_channels`).

Flaky test issues are not here: every recorded run already opens and closes them
when ``TESTO_ISSUES=github`` (:mod:`~qasight.services.flaky_issues`).

Everything is best-effort: a failed step is one warning line, never a changed
exit code, because the tests already ran and were recorded.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from qasight.services.ci_provenance import detect_ci_provenance

Provider = Literal["github", "gitlab", "other"]
GITHUB_TOKEN_ENVS = ("TESTO_GITHUB_TOKEN", "GITHUB_TOKEN")
GITLAB_TOKEN_ENV = "TESTO_GITLAB_TOKEN"
_PR_EVENTS = {"pull_request", "pull_request_target"}
_MAX_PAGES = 10


class PublishError(RuntimeError):
    """A call to the CI provider's API failed (the message says which)."""


@dataclass(frozen=True)
class CIContext:
    provider: Provider
    # The pull request (GitHub) or merge request (GitLab) this job tests, if any.
    review: int | None = None
    run_url: str | None = None


@dataclass(frozen=True)
class PublishOptions:
    job_summary: bool = True
    comment: bool = True
    annotations: bool = True
    # "failure" | "change" | "always", or None for no chat alert.
    notify_when: str | None = "failure"
    ai: bool = False
    out: Path | None = None
    # GitHub, pull requests from forks: leave the comment here instead of posting it.
    comment_dir: Path | None = None
    workspace: Path = Path(".")
    config_path: Path | None = None


def _github_review(env: Mapping[str, str]) -> int | None:
    if env.get("GITHUB_EVENT_NAME") not in _PR_EVENTS:
        return None
    try:
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        return int(event["pull_request"]["number"])
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return None


def ci_run_url(env: Mapping[str, str]) -> str | None:
    """A link to this CI job or pipeline, for the summary's footer and the alerts."""
    server, repo, run_id = (
        env.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    for var in ("CI_JOB_URL", "CI_PIPELINE_URL", "CIRCLE_BUILD_URL", "BUILDKITE_BUILD_URL"):
        if url := (env.get(var) or "").strip():
            return url
    return (env.get("BUILD_URL") or "").strip() or None  # Jenkins


def context(env: Mapping[str, str]) -> CIContext:
    """Which CI this job runs in, the review it belongs to, and a link to it."""
    provenance = detect_ci_provenance(env)
    provider: Provider = "other"
    review: int | None = None
    if provenance is not None and provenance.ci_provider == "github":
        provider, review = "github", _github_review(env)
    elif provenance is not None and provenance.ci_provider == "gitlab":
        provider, review = "gitlab", _int(env.get("CI_MERGE_REQUEST_IID"))
    return CIContext(provider=provider, review=review, run_url=ci_run_url(env))


def _call(method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None) -> Any:
    import requests

    try:
        resp = requests.request(method, url, headers=headers, json=body, timeout=30)
    except requests.RequestException as exc:
        raise PublishError(f"{type(exc).__name__} calling the API") from None
    if resp.status_code >= 400:
        raise PublishError(f"the API answered HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json() if resp.content else None


def upsert_github_comment(*, markdown: str, number: int, token: str, env: Mapping[str, str]) -> str:
    """Edit this cycle's earlier comment on the pull request, or post a new one."""
    api = (env.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    base = f"{api}/repos/{env['GITHUB_REPOSITORY']}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    marker = markdown.splitlines()[0]
    for page in range(1, _MAX_PAGES + 1):
        comments = _call("GET", f"{base}/{number}/comments?per_page=100&page={page}", headers, None)
        for comment in comments:
            if str(comment.get("body") or "").startswith(marker):
                _call("PATCH", f"{base}/comments/{comment['id']}", headers, {"body": markdown})
                return "updated"
        if len(comments) < 100:
            break
    _call("POST", f"{base}/{number}/comments", headers, {"body": markdown})
    return "created"


def upsert_gitlab_note(*, markdown: str, iid: int, token: str, env: Mapping[str, str]) -> str:
    """Edit this cycle's earlier note on the merge request, or post a new one."""
    api = (env.get("CI_API_V4_URL") or "https://gitlab.com/api/v4").rstrip("/")
    project = urllib.parse.quote(str(env["CI_PROJECT_ID"]), safe="")
    base = f"{api}/projects/{project}/merge_requests/{iid}/notes"
    headers = {"PRIVATE-TOKEN": token}
    marker = markdown.splitlines()[0]
    for page in range(1, _MAX_PAGES + 1):
        notes = _call("GET", f"{base}?per_page=100&page={page}", headers, None)
        for note in notes:
            if str(note.get("body") or "").startswith(marker):
                _call("PUT", f"{base}/{note['id']}", headers, {"body": markdown})
                return "updated"
        if len(notes) < 100:
            break
    _call("POST", base, headers, {"body": markdown})
    return "created"


def _token(env: Mapping[str, str], names: tuple[str, ...]) -> str:
    return next((env[k].strip() for k in names if (env.get(k) or "").strip()), "")


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in value).strip("-")


def _warn_stderr(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _comment(
    markdown: str, *, cycle: str, ctx: CIContext, opts: PublishOptions, env: Mapping[str, str]
) -> str | None:
    """Post or edit the review comment; returns what happened, or ``None`` when there's no review."""
    if ctx.review is None:
        return None
    if ctx.provider == "github":
        if opts.comment_dir is not None:
            opts.comment_dir.mkdir(parents=True, exist_ok=True)
            path = opts.comment_dir / f"{_slug(cycle) or 'run'}.md"
            path.write_text(markdown, encoding="utf-8")
            return f"left for the comment workflow in {path}"
        token = _token(env, GITHUB_TOKEN_ENVS)
        if not token:
            raise PublishError(f"commenting needs {' or '.join(GITHUB_TOKEN_ENVS)}")
        done = upsert_github_comment(markdown=markdown, number=ctx.review, token=token, env=env)
        return f"pull request #{ctx.review} comment {done}"
    if ctx.provider == "gitlab":
        token = _token(env, (GITLAB_TOKEN_ENV,))
        if not token:
            raise PublishError(
                f"commenting on a merge request needs {GITLAB_TOKEN_ENV} (api scope)"
            )
        done = upsert_gitlab_note(markdown=markdown, iid=ctx.review, token=token, env=env)
        return f"merge request !{ctx.review} comment {done}"
    return None


def _annotations(cycle: str, opts: PublishOptions) -> list[str]:
    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load
    from qasight.services.run_annotations import load_failures, workflow_commands
    from qasight.services.run_summary_markdown import load_run_summary

    summary = load_run_summary(cycle=cycle)
    if summary is None:
        return []
    roots: list[Path] = []
    try:
        plan = discover_and_load(config_path=opts.config_path).cycles.get(summary.run.cycle or "")
        if plan is not None:
            roots = [Path(s.target_repo) for s in plan.stages]
    except ConfigError:
        pass
    failures = load_failures(summary.run, workspace=opts.workspace, roots=roots)
    return workflow_commands(failures, cycle=summary.run.cycle)


def configured_alert_channels(config_path: Path | None) -> set[str]:
    """Chat channels ``qasight.yaml`` already alerts through (its ``slack`` / ``teams`` reporters).

    Those fire inside ``qasight run`` and ``qasight ingest``, so publishing skips them:
    the alert is configured in one place and sent once.
    """
    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load
    from qasight.services import run_alerts

    try:
        cfg = discover_and_load(config_path=config_path)
    except (ConfigError, OSError):
        return set()
    return {r.type for r in cfg.reporters if r.type in run_alerts.CHANNELS}


def _notify(
    cycle: str, when: str, ctx: CIContext, env: Mapping[str, str], opts: PublishOptions
) -> list[str]:
    from qasight.services import run_alerts

    sent: list[str] = []
    configured = configured_alert_channels(opts.config_path)
    channels = [
        c
        for c in run_alerts.CHANNELS
        if c not in configured and run_alerts.webhook_from_env(c, dict(env))
    ]
    if not channels or when not in run_alerts.WHEN_CHOICES:
        return sent
    alert = run_alerts.load_alert(cycle=cycle)
    if alert is None or not run_alerts.should_send(alert, when):  # type: ignore[arg-type]
        return sent
    for channel in channels:
        payload = run_alerts.build_payload(channel, alert, title=None, run_url=ctx.run_url)
        run_alerts.post(run_alerts.webhook_from_env(channel, dict(env)), payload)
        sent.append(channel)
    return sent


def publish(
    *,
    cycle: str | None,
    env: Mapping[str, str],
    opts: PublishOptions,
    echo: Callable[[str], None] = print,
    warn: Callable[[str], None] = _warn_stderr,
) -> bool:
    """Publish ``cycle``'s latest recorded run (any cycle's when ``None``).

    Returns ``False`` when there is no such run. Annotations and alerts need a cycle.
    """
    from qasight.services.run_summary_markdown import load_run_summary, render_markdown

    ctx = context(env)
    summary = load_run_summary(cycle=cycle or None)
    if summary is None:
        warn(f"no recorded run of {cycle!r} to publish" if cycle else "no recorded run to publish")
        return False
    markdown = render_markdown(summary, run_url=ctx.run_url)
    if opts.ai and summary.run.returncode != 0:
        from qasight.services.ai_run_summary import summarize
        from qasight.services.run_summary_markdown import ai_section

        found = summarize(run_id=summary.run.run_id)
        markdown = ai_section(markdown, found.text, found.reason)

    if opts.out is not None:
        opts.out.parent.mkdir(parents=True, exist_ok=True)
        opts.out.write_text(markdown, encoding="utf-8")
    step_summary = env.get("GITHUB_STEP_SUMMARY")
    if opts.job_summary and ctx.provider == "github" and step_summary:
        with Path(step_summary).open("a", encoding="utf-8") as fh:
            fh.write(markdown)

    steps: list[tuple[str, Callable[[], None]]] = []
    if opts.comment:

        def comment() -> None:
            if done := _comment(markdown, cycle=cycle or "", ctx=ctx, opts=opts, env=env):
                echo(done)

        steps.append(("comment", comment))
    if opts.annotations and ctx.provider == "github" and cycle:

        def annotate() -> None:
            for line in _annotations(str(cycle), opts):
                echo(line)

        steps.append(("annotations", annotate))
    if opts.notify_when and cycle:

        def notify() -> None:
            for channel in _notify(str(cycle), str(opts.notify_when), ctx, env, opts):
                echo(f"{channel}: sent")

        steps.append(("chat alert", notify))
    for name, step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - best-effort: the tests already ran
            warn(f"could not send the {name}: {exc}")
    return True
