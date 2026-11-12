"""GitHub Actions wrapper around ``qasight run --ci`` and ``qasight ingest --ci``.

With a ``qasight.yaml`` it runs one cycle (or every cycle, for ``all``).
With no config file at all, or when the ``results`` input names files, it
records result files an earlier step wrote instead (``qasight ingest``; with no
``results`` it finds them itself), so a workflow needs no config to get the
summary and the pull request comment. Either way it echoes the NDJSON event
stream and turns the final ``plan_finished`` event into step outputs
(``exit_code``, ``status``, ``summary_json``, ``summary_path``).

Before that, ``prepare-history`` decides where the run history lives: unless
``DATABASE_URL`` or the config's ``database.url`` says otherwise, a SQLite
file under ``~/.cache/qasight`` that the action restores from and saves
to the Actions cache, so each run is compared with the ones before it.

With the run persisted, it also renders the run as Markdown
(``qasight report markdown``) for the job summary and, when asked, for one
pull request comment per cycle that later runs edit in place; prints failing
tests as annotations on the diff (``qasight report annotations``); sends a Slack
or Teams alert when a webhook secret is given. Those steps are ``qasight ci
publish``, the same command any other CI runs; with a pinned qasight older than
it, the action runs them one by one. With ``flaky-issues`` the run itself opens
one issue per newly flaky test (``TESTO_ISSUES=github``). All of it is
best-effort: a failure there is a warning, never a changed exit code.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_COMMENT_EVENTS = {"pull_request", "pull_request_target"}


def _parse_bool(value: str, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def build_command(
    *, config_path: str, cycle: str, ci_mode: bool, persist: bool, coverage: str = ""
) -> list[str]:
    cmd = ["qasight", "run"]
    if config_path:
        cmd += ["--config", config_path]
    if cycle:
        cmd += ["--cycle", cycle]
    if ci_mode:
        cmd.append("--ci")
    if not persist:
        cmd.append("--no-persist")
    for report in coverage.split():
        cmd += ["--coverage", report]
    return cmd


# The testosterone names are from before the rename to QASight (1.6).
CONFIG_NAMES = ("qasight.yaml", "qasight.yml", "testosterone.yaml", "testosterone.yml")
# Kept from before the rename so the Actions cache of existing users still restores.
HISTORY_DIR = Path("~/.cache/testosterone")


def has_config(workspace: Path, config_path: str) -> bool:
    """Whether a QASight config is given or would be discovered in *workspace*."""
    if config_path:
        return True
    if any((workspace / name).is_file() for name in CONFIG_NAMES):
        return True
    try:
        pyproject = (workspace / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return "[tool.qasight]" in pyproject or "[tool.testosterone]" in pyproject


def split_results(value: str) -> list[str]:
    """The ``results`` input: patterns separated by newlines or spaces."""
    return [part for part in str(value or "").split() if part]


def build_ingest_command(
    *, results: list[str], cycle: str, ci_mode: bool, persist: bool, coverage: str = ""
) -> list[str]:
    cmd = ["qasight", "ingest", *results]
    if cycle and cycle != "all":
        cmd += ["--cycle", cycle]
    if ci_mode:
        cmd.append("--ci")
    if not persist:
        cmd.append("--no-persist")
    for report in coverage.split():
        cmd += ["--coverage", report]
    return cmd


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in value).strip("-") or "job"


def prepare_history(*, history: str, config_path: str, env: dict[str, str]) -> dict[str, str]:
    """Step outputs that say whether to cache the run history, where, and under which key.

    ``database_url`` is always set: the user's own ``DATABASE_URL`` when there is
    one (so passing it on changes nothing), else the cached SQLite file. Only the
    database is cached (per-run report copies would grow the cache every run).
    """
    outputs = {
        "cache": "false",
        "dir": "",
        "key": "",
        "restore_prefix": "",
        "database_url": env.get("DATABASE_URL", ""),
    }
    if (
        history.strip().lower() in {"off", "false", "no", "0"}
        or env.get("DATABASE_URL", "").strip()
    ):
        return outputs
    workspace = Path(env.get("GITHUB_WORKSPACE") or ".")
    try:
        from qasight.config.database_section import (
            database_url_from_discovered_config,
            extract_database_url_from_mapping,
            load_raw_config_dict,
        )

        if config_path:
            raw = load_raw_config_dict(workspace / config_path)
            configured = extract_database_url_from_mapping(raw)
        else:
            configured = database_url_from_discovered_config(cwd=workspace)
    except Exception:  # noqa: BLE001 - an older qasight or a broken config: no cache, no harm
        return outputs
    if configured:
        return outputs
    folder = HISTORY_DIR.expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    prefix = "testo-history-{}-{}-".format(
        _slug(env.get("GITHUB_WORKFLOW", "")), _slug(env.get("GITHUB_JOB", ""))
    )
    # The step id too: a job that uses the action twice saves two caches, not one twice.
    run = "{}-{}-{}".format(
        env.get("GITHUB_RUN_ID", "0"),
        env.get("GITHUB_RUN_ATTEMPT", "1"),
        _slug(env.get("GITHUB_ACTION", "")),
    )
    outputs.update(
        {
            "cache": "true",
            "dir": str(folder),
            "key": prefix + run,
            "restore_prefix": prefix,
            "database_url": f"sqlite:///{(folder / 'testo_history.db').as_posix()}",
        }
    )
    return outputs


def _extract_summary(stdout: str, *, fallback_exit_code: int) -> dict[str, Any]:
    """Return the last NDJSON object carrying an ``exit_code`` (``plan_finished`` or ``error``)."""
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "exit_code" in payload:
            return payload
        if isinstance(payload, dict) and payload.get("event") == "error":
            # ``{"event": "error", "code": ..., "message": ...}`` carries no exit code.
            return {**payload, "exit_code": int(fallback_exit_code)}
    return {
        "event": "error",
        "error": "Unable to parse a plan_finished event from qasight output.",
        "exit_code": int(fallback_exit_code),
    }


def finished_cycles(stdout: str) -> list[str]:
    """The cycles a ``plan_finished`` event was printed for, in order (several for ``all``)."""
    cycles: list[str] = []
    for line in stdout.splitlines():
        try:
            payload = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("event") == "plan_finished":
            plan = str(payload.get("plan") or "")
            if plan and plan not in cycles:
                cycles.append(plan)
    return cycles


def _status_from_exit_code(exit_code: int) -> str:
    if exit_code == 0:
        return "success"
    if exit_code == 1:
        return "failure"
    if exit_code == 2:
        return "invalid_input"
    if exit_code == 3:
        return "infra_failure"
    return "internal_error"


def _write_github_outputs(path: Path, *, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def _warn(message: str) -> None:
    print(f"::warning title=testo::{message}", file=sys.stderr)


def render_markdown(*, cycle: str, env: dict[str, str]) -> str | None:
    """``qasight report markdown`` for the run that just finished, or ``None``."""
    cmd = ["qasight", "report", "markdown"]
    if cycle and cycle != "all":
        cmd += ["--cycle", cycle]
    server, repo, run_id = (
        env.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    if server and repo and run_id:
        cmd += ["--run-url", f"{server}/{repo}/actions/runs/{run_id}"]
    if _parse_bool(env.get("TESTO_INPUT_AI_SUMMARY", ""), default=False):
        cmd.append("--ai")
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, env=env)  # noqa: S603
    if proc.returncode != 0 or not proc.stdout.strip():
        _warn(f"could not render the run summary: {proc.stderr.strip() or proc.returncode}")
        return None
    return proc.stdout


def pull_request_number(env: dict[str, str]) -> int | None:
    """The pull request this workflow run belongs to, from the event payload."""
    if env.get("GITHUB_EVENT_NAME") not in _COMMENT_EVENTS:
        return None
    try:
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        return int(event["pull_request"]["number"])
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _github(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(  # noqa: S310 - the GitHub API URL comes from the runner
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read() or b"null")


def upsert_pr_comment(*, markdown: str, number: int, token: str, env: dict[str, str]) -> str:
    """Edit this cycle's earlier comment on the pull request, or post a new one."""
    api = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    base = f"{api}/repos/{env['GITHUB_REPOSITORY']}/issues"
    marker = markdown.splitlines()[0]
    for page in range(1, 11):
        comments = _github("GET", f"{base}/{number}/comments?per_page=100&page={page}", token)
        for comment in comments:
            if str(comment.get("body") or "").startswith(marker):
                _github("PATCH", f"{base}/comments/{comment['id']}", token, {"body": markdown})
                return "updated"
        if len(comments) < 100:
            break
    _github("POST", f"{base}/{number}/comments", token, {"body": markdown})
    return "created"


def publish_summary(*, cycle: str, job_summary: bool, comment: bool, env: dict[str, str]) -> None:
    if not (job_summary or comment):
        return
    markdown = render_markdown(cycle=cycle, env=env)
    if markdown is None:
        return
    step_summary = env.get("GITHUB_STEP_SUMMARY")
    if job_summary and step_summary:
        with Path(step_summary).open("a", encoding="utf-8") as fh:
            fh.write(markdown)
    if not comment:
        return
    number = pull_request_number(env)
    if number is not None and _parse_bool(
        env.get("TESTO_INPUT_COMMENT_ARTIFACT", ""), default=False
    ):
        write_comment_artifact(markdown=markdown, cycle=cycle, env=env)
        return
    token = env.get("TESTO_GITHUB_TOKEN", "")
    if number is None or not token:
        return
    try:
        upsert_pr_comment(markdown=markdown, number=number, token=token, env=env)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        # A fork's pull request gets a read-only token: say so, keep the job's result.
        _warn(f"could not comment on pull request #{number} ({exc}); it needs pull-requests: write")


COMMENT_MARKER = "<!-- testo-summary:"
# What a comment artifact may hold: a fork's workflow wrote it, so it is untrusted.
MAX_COMMENT_FILES = 20
MAX_COMMENT_BYTES = 60_000


def comment_dir(env: dict[str, str]) -> Path:
    return Path(env.get("RUNNER_TEMP") or ".") / "testo-comment"


def write_comment_artifact(*, markdown: str, cycle: str, env: dict[str, str]) -> Path:
    """Leave the comment for a trusted ``workflow_run`` job instead of posting it.

    A fork's pull request runs with a read-only token, so the comment goes into
    ``$RUNNER_TEMP/testo-comment/<cycle>.md``; the action uploads that folder as the
    ``testo-comment`` artifact (``comment_dir`` output) and ``post-comments`` posts it.
    """
    folder = comment_dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_slug(cycle) or 'run'}.md"
    path.write_text(markdown, encoding="utf-8")
    github_output = env.get("GITHUB_OUTPUT")
    if github_output:
        _write_github_outputs(Path(github_output), values={"comment_dir": str(folder)})
    return path


def safe_comment(text: str) -> str | None:
    """*text* if it is a QASight comment of a sane size, with mentions defused."""
    if not text.startswith(COMMENT_MARKER) or len(text.encode("utf-8")) > MAX_COMMENT_BYTES:
        return None
    first, _, rest = text.partition("\n")
    # The first line is the marker that later runs find the comment by; keep it as is.
    return first + "\n" + rest.replace("@", "&#64;")


def triggering_pull_request(*, token: str, env: dict[str, str]) -> int | None:
    """The open pull request whose head is the commit the ``workflow_run`` ran on.

    Found from the trusted event payload and the API, never from the artifact, so
    a fork can't point its comment at another pull request.
    """
    try:
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        run = event["workflow_run"]
        head_sha = str(run["head_sha"])
    except (KeyError, OSError, TypeError, ValueError):
        return None
    for pr in run.get("pull_requests") or []:
        # Filled in for branches of this repository; empty for forks.
        if str((pr.get("head") or {}).get("sha")) == head_sha:
            return int(pr["number"])
    api = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    repo = env["GITHUB_REPOSITORY"]
    for page in range(1, 6):
        pulls = _github(
            "GET", f"{api}/repos/{repo}/pulls?state=open&per_page=100&page={page}", token
        )
        for pr in pulls:
            if str((pr.get("head") or {}).get("sha")) == head_sha:
                return int(pr["number"])
        if len(pulls) < 100:
            break
    return None


def post_comments(*, folder: Path, env: dict[str, str]) -> int:
    """Post the comments a ``comment-artifact`` run left in *folder*. Returns how many."""
    token = env.get("TESTO_GITHUB_TOKEN", "")
    if not token:
        _warn("post-comments-from needs github-token with pull-requests: write")
        return 0
    files = sorted(p for p in folder.glob("*.md") if p.is_file())[:MAX_COMMENT_FILES]
    if not files:
        print(f"No comments in {folder}: the run had nothing to post.")
        return 0
    try:
        number = triggering_pull_request(token=token, env=env)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        _warn(f"could not find the pull request to comment on ({exc})")
        return 0
    if number is None:
        _warn("found no open pull request whose head is the commit that run tested")
        return 0
    posted = 0
    for path in files:
        body = safe_comment(path.read_text(encoding="utf-8", errors="replace"))
        if body is None:
            _warn(f"skipped {path.name}: not a QASight comment, or too large")
            continue
        try:
            upsert_pr_comment(markdown=body, number=number, token=token, env=env)
            posted += 1
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            _warn(f"could not comment on pull request #{number} ({exc})")
    return posted


def _testo(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["qasight", *cmd], check=False, capture_output=True, text=True, env=env
    )


def _run_url(env: dict[str, str]) -> str | None:
    server, repo, run_id = (
        env.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None


def annotate(*, cycle: str, config_path: str, env: dict[str, str]) -> None:
    """Print ``qasight report annotations`` so GitHub puts failing tests on the diff."""
    cmd = ["report", "annotations", "--cycle", cycle]
    if env.get("GITHUB_WORKSPACE"):
        cmd += ["--workspace", env["GITHUB_WORKSPACE"]]
    if config_path:
        cmd += ["--config", config_path]
    proc = _testo(cmd, env)
    if proc.returncode != 0:
        _warn(f"could not annotate failing tests: {proc.stderr.strip() or proc.returncode}")
        return
    sys.stdout.write(proc.stdout)


def notify(*, cycle: str, when: str, env: dict[str, str]) -> None:
    """``qasight notify`` to every chat whose webhook secret was passed in."""
    channels = [
        c for c in ("slack", "teams") if env.get(f"TESTO_{c.upper()}_WEBHOOK_URL", "").strip()
    ]
    if not channels:
        return
    cmd = ["notify", "--cycle", cycle, "--when", when or "failure"]
    for channel in channels:
        cmd += ["--to", channel]
    run_url = _run_url(env)
    if run_url:
        cmd += ["--run-url", run_url]
    proc = _testo(cmd, env)
    if proc.returncode != 0:
        _warn(f"could not send the chat alert: {proc.stderr.strip() or proc.returncode}")


def ci_publish(
    *,
    cycle: str,
    job_summary: bool,
    comment: bool,
    annotations: bool,
    notify_when: str,
    config_path: str,
    env: dict[str, str],
) -> bool:
    """``qasight ci publish``: summary, comment, annotations and alert, as on any CI.

    Returns ``False`` when the installed qasight has no ``ci`` command (a pinned
    older version), so the caller publishes step by step instead.
    """
    cmd = ["ci", "publish"]
    if cycle:
        cmd += ["--cycle", cycle]
    cmd += ["--comment" if comment else "--no-comment"]
    cmd += ["--job-summary" if job_summary else "--no-job-summary"]
    cmd += ["--annotations" if annotations else "--no-annotations"]
    cmd += ["--notify-when", notify_when or "failure"]
    if _parse_bool(env.get("TESTO_INPUT_AI_SUMMARY", ""), default=False):
        cmd.append("--ai")
    artifact = comment and _parse_bool(env.get("TESTO_INPUT_COMMENT_ARTIFACT", ""), default=False)
    if artifact and pull_request_number(env) is not None:
        cmd += ["--comment-dir", str(comment_dir(env))]
    if env.get("GITHUB_WORKSPACE"):
        cmd += ["--workspace", env["GITHUB_WORKSPACE"]]
    if config_path:
        cmd += ["--config", config_path]
    proc = _testo(cmd, env)
    if proc.returncode == 2 and "No such command" in (proc.stderr or ""):
        return False
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    if artifact and comment_dir(env).is_dir() and env.get("GITHUB_OUTPUT"):
        _write_github_outputs(
            Path(env["GITHUB_OUTPUT"]), values={"comment_dir": str(comment_dir(env))}
        )
    return True


def history_note(env: dict[str, str]) -> str | None:
    """A job summary line when the Actions cache had no earlier history to restore."""
    if env.get("TESTO_HISTORY_CACHED") != "true" or env.get("TESTO_HISTORY_RESTORED_KEY"):
        return None
    return (
        "> No earlier run history was found in the Actions cache, so this run has nothing to "
        "be compared with: it is the first run of this workflow job, or GitHub removed the "
        "cache (caches unused for 7 days are deleted). Later runs are compared with this one."
    )


def stage_result_dirs(*, workspace: Path, config_path: str, cycle: str) -> list[str]:
    """The ``allure-results`` folders ``qasight run`` wrote for *cycle*, one per stage."""
    try:
        from qasight.config.loader import discover_and_load
        from qasight.paths import plan_artifacts_dir

        cfg = discover_and_load(
            config_path=(workspace / config_path) if config_path else None, cwd=workspace
        )
        root = Path(str(cfg.defaults.artifacts_root or "artifacts"))
        if not root.is_absolute():
            root = workspace / root
        plan_dir = plan_artifacts_dir(root, cycle)
    except Exception:  # noqa: BLE001 - an older qasight or a broken config: nothing to upload
        return []
    return sorted(str(d) for d in plan_dir.glob("*/allure-results") if d.is_dir())


def cycle_stage_name(result_dirs: list[str]) -> str:
    """The stage name for the upload: the stage's own when the cycle has one, else ``junit``."""
    return Path(result_dirs[0]).parent.name if len(result_dirs) == 1 else ""


def upload_to_server(
    *, cycle: str, reports: list[str], stage: str, server: str, token: str, env: dict[str, str]
) -> None:
    """Send the same results to a team server with ``qasight ingest --server`` (best effort)."""
    cmd = ["ingest", *reports, "--server", server, "--ci"]
    if cycle:
        cmd += ["--cycle", cycle]
    if stage:
        cmd += ["--stage", stage]
    upload_env = {**env, "TESTO_API_TOKEN": token} if token else dict(env)
    proc = _testo(cmd, upload_env)
    # 1 = tests failed, which the server recorded like any other run.
    if proc.returncode not in (0, 1):
        _warn(
            f"could not upload the {cycle or 'results'} run to {server} (exit code "
            f"{proc.returncode}): {(proc.stderr or proc.stdout).strip()[-300:]}"
        )
    else:
        print(f"Uploaded the {cycle or 'results'} run to {server}")


def pages_base_path(repository: str) -> str:
    """Where GitHub Pages serves ``owner/name``: ``/`` for ``owner.github.io``, else ``/name/``.

    Same rule as ``qasight_api.static_site.github_pages_base_path``; this script
    must run before testo's api extra is installed, so it can't import it.
    """
    owner, _, name = repository.partition("/")
    if name.lower() == f"{owner}.github.io".lower():
        return "/"
    return f"/{name}/"


def api_requirements() -> list[str]:
    """The installed qasight's ``api`` extra, so its pins match the installed version."""
    from importlib import metadata

    reqs: list[str] = []
    for req in metadata.requires("qasight") or []:
        name, _, marker = req.partition(";")
        if 'extra == "api"' in marker.replace("'", '"'):
            reqs.append(name.strip())
    return reqs


def export_static_site(*, out: Path, base_path: str, env: dict[str, str]) -> int:
    """Install the api extra's packages, then ``qasight export-site`` into ``out``.

    Writes the ``site_dir`` output for the Pages upload step. A failure is a
    warning, not a red job: the tests already ran and were recorded.
    """
    base = base_path.strip() or pages_base_path(env.get("GITHUB_REPOSITORY", ""))
    reqs = api_requirements()
    if reqs:
        subprocess.run(  # noqa: S603 — this interpreter's pip, requirements from metadata
            [sys.executable, "-m", "pip", "install", "--quiet", *reqs], check=False
        )
    proc = subprocess.run(  # noqa: S603 — fixed argv
        ["qasight", "export-site", "--out", str(out), "--base-path", base], check=False, env=env
    )
    if proc.returncode != 0:
        _warn(f"qasight export-site failed (exit {proc.returncode}); no static site this run")
        return 0
    github_output = env.get("GITHUB_OUTPUT")
    if github_output:
        _write_github_outputs(Path(github_output), values={"site_dir": str(out)})
    return 0


SCORECARD_MARKER = "<!-- qasight-scorecard -->"


def build_scorecard_scan_command(
    *, path: str, out: Path, min_score: str, no_new: bool, tools: str
) -> list[str]:
    cmd = ["qasight", "scorecard", "scan", path.strip() or ".", "--out", str(out)]
    if min_score.strip():
        cmd += ["--min-score", min_score.strip()]
    if no_new:
        cmd.append("--no-new")
    if tools.strip():
        cmd += ["--tools", tools.strip()]
    return cmd


def run_scorecard(
    *,
    path: str,
    min_score: str,
    no_new: bool,
    tools: str,
    job_summary: bool,
    comment: bool,
    env: dict[str, str],
) -> int:
    """``qasight scorecard scan`` with the gate, then the Markdown summary (job summary and
    one pull request comment), and SARIF, HTML and a badge next to it. Returns the scan's
    exit code, so a failed gate fails the step after everything is published."""
    out = Path(env.get("RUNNER_TEMP") or ".") / "qasight-scorecard"
    out.mkdir(parents=True, exist_ok=True)
    cmd = build_scorecard_scan_command(
        path=path, out=out, min_score=min_score, no_new=no_new, tools=tools
    )
    scan = subprocess.run(cmd, check=False, env=env)  # noqa: S603 - fixed argv, inputs as args
    written: dict[str, str] = {
        "scorecard_dir": str(out),
        "scorecard_exit_code": str(scan.returncode),
    }
    for fmt, name in (
        ("sarif", "scorecard.sarif"),
        ("html", "scorecard.html"),
        ("svg", "badge.svg"),
    ):
        proc = _testo(["scorecard", "show", "--format", fmt, "-o", str(out / name)], env)
        if proc.returncode in (0, 1):  # 1 = a gate failed; the file is still written
            written[f"scorecard_{fmt}"] = str(out / name)
        else:
            _warn(
                f"could not write the scorecard as {fmt}: {proc.stderr.strip() or proc.returncode}"
            )
    shown = _testo(["scorecard", "show", "--format", "markdown"], env)
    markdown = shown.stdout.strip()
    if markdown:
        body = f"{SCORECARD_MARKER}\n{markdown}\n"
        step_summary = env.get("GITHUB_STEP_SUMMARY")
        if job_summary and step_summary:
            with Path(step_summary).open("a", encoding="utf-8") as fh:
                fh.write(body)
        number = pull_request_number(env)
        token = env.get("TESTO_GITHUB_TOKEN", "")
        if comment and number is not None and token:
            try:
                upsert_pr_comment(markdown=body, number=number, token=token, env=env)
            except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
                _warn(
                    f"could not comment on pull request #{number} ({exc}); it needs pull-requests: write"
                )
    github_output = env.get("GITHUB_OUTPUT")
    if github_output:
        _write_github_outputs(Path(github_output), values=written)
    return scan.returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["export-site"]:
        exporting = argparse.ArgumentParser(description="Export the run history as a site.")
        exporting.add_argument("--out", required=True)
        exporting.add_argument("--base-path", default="")
        parsed = exporting.parse_args(argv[1:])
        return export_static_site(
            out=Path(parsed.out), base_path=parsed.base_path, env=dict(os.environ)
        )
    if argv[:1] == ["prepare-history"]:
        return _prepare_history_main(argv[1:])
    if argv[:1] == ["scorecard"]:
        scoring = argparse.ArgumentParser(description="Score the repository (qasight scorecard).")
        scoring.add_argument("--path", default=".")
        scoring.add_argument("--min-score", default="")
        scoring.add_argument("--no-new", default="false")
        scoring.add_argument("--tools", default="")
        scoring.add_argument("--job-summary", default="true")
        scoring.add_argument("--comment-on-pr", default="true")
        opts = scoring.parse_args(argv[1:])
        return run_scorecard(
            path=opts.path,
            min_score=opts.min_score,
            no_new=_parse_bool(opts.no_new, default=False),
            tools=opts.tools,
            job_summary=_parse_bool(opts.job_summary, default=True),
            comment=_parse_bool(opts.comment_on_pr, default=True),
            env=dict(os.environ),
        )
    if argv[:1] == ["post-comments"]:
        posting = argparse.ArgumentParser(description="Post a fork run's comments.")
        posting.add_argument("--dir", required=True)
        folder = Path(posting.parse_args(argv[1:]).dir)
        print(f"Posted {post_comments(folder=folder, env=dict(os.environ))} comment(s).")
        return 0
    parser = argparse.ArgumentParser(description="Run a QASight cycle in GitHub Actions.")
    parser.add_argument("--config-path", default="")
    parser.add_argument("--cycle", default="")
    parser.add_argument("--ci-mode", default="true")
    parser.add_argument("--persist", default="true")
    parser.add_argument("--summary-path", default="")
    parser.add_argument("--job-summary", default="true")
    parser.add_argument("--comment-on-pr", default="auto")
    parser.add_argument("--results", default="")
    parser.add_argument("--annotations", default="true")
    parser.add_argument("--notify-when", default="failure")
    parser.add_argument("--flaky-issues", default="false")
    parser.add_argument("--coverage", default="")
    args = parser.parse_args(argv)

    persist = _parse_bool(args.persist, default=True)
    config_path = str(args.config_path or "").strip()
    results = split_results(args.results)
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or ".")
    # No config to run, or result files named: record what an earlier step wrote.
    ingest = bool(results) or not has_config(workspace, config_path)
    if ingest:
        cmd = build_ingest_command(
            results=results,
            cycle=str(args.cycle or "").strip(),
            ci_mode=_parse_bool(args.ci_mode, default=True),
            persist=persist,
            coverage=str(args.coverage or ""),
        )
    else:
        cmd = build_command(
            config_path=config_path,
            cycle=str(args.cycle or "").strip(),
            ci_mode=_parse_bool(args.ci_mode, default=True),
            persist=persist,
            coverage=str(args.coverage or ""),
        )
    # ``auto``: comment when there is no config to opt in with (zero-config), keep the
    # old default (off) for configured runs.
    comment = (
        ingest
        if str(args.comment_on_pr).strip().lower() == "auto"
        else _parse_bool(args.comment_on_pr, default=False)
    )

    server = os.environ.get("TESTO_INPUT_SERVER_URL", "").strip()
    run_env = os.environ.copy()
    if _parse_bool(args.flaky_issues, default=False):
        # Every recorded run opens an issue per newly flaky test and closes fixed ones
        # (qasight.services.flaky_issues), with the token the action passes in.
        run_env.setdefault("TESTO_ISSUES", "github")
    if server:
        # The local run feeds the summary and comment; the upload is its own step below.
        run_env.pop("TESTO_SERVER_URL", None)
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, env=run_env)  # noqa: S603
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)

    summary = _extract_summary(proc.stdout or "", fallback_exit_code=int(proc.returncode))
    summary_json = json.dumps(summary, separators=(",", ":"), ensure_ascii=True)

    summary_path_arg = str(args.summary_path or "").strip()
    if not summary_path_arg:
        runner_temp = os.getenv("RUNNER_TEMP", ".")
        summary_path = Path(runner_temp) / "testo-summary.json"
    else:
        summary_path = Path(summary_path_arg)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary_json + "\n", encoding="utf-8")

    outputs = {
        "exit_code": str(proc.returncode),
        "summary_json": summary_json,
        "summary_path": str(summary_path),
        "status": _status_from_exit_code(int(proc.returncode)),
    }
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        _write_github_outputs(Path(github_output), values=outputs)

    # Everything below reads the run back from the run history, which --no-persist skips.
    # Without a plan_finished event nothing was recorded, and "the latest run" would be
    # an earlier job's (restored from the cache) shown as if it were this one.
    finished = summary.get("event") == "plan_finished"
    if persist and not finished:
        _warn(
            f"qasight {'ingest' if ingest else 'run'} did not finish a run (exit code "
            f"{proc.returncode}); no summary, comment, annotations or alert were published"
        )
    if persist and finished:
        env = os.environ.copy()
        requested = str(args.cycle or "").strip()
        # ``all`` runs every cycle: each gets its own summary, comment, annotations and alert.
        if requested == "all" and not ingest:
            cycles = finished_cycles(proc.stdout or "")
        else:
            cycles = [
                str(summary.get("plan") or "")
                if ingest
                else requested or str(summary.get("plan") or "")
            ]
        note = history_note(env)
        if note and env.get("GITHUB_STEP_SUMMARY"):
            with Path(env["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as fh:
                fh.write(note + "\n\n")
        for cycle in cycles:
            published = ci_publish(
                cycle=cycle,
                job_summary=_parse_bool(args.job_summary, default=True),
                comment=comment,
                annotations=_parse_bool(args.annotations, default=True),
                notify_when=str(args.notify_when or "").strip(),
                config_path=config_path,
                env=env,
            )
            if not published:
                # A pinned qasight older than ``qasight ci publish``: the steps one by one.
                publish_summary(
                    cycle=cycle,
                    job_summary=_parse_bool(args.job_summary, default=True),
                    comment=comment,
                    env=env,
                )
                if cycle and _parse_bool(args.annotations, default=True):
                    annotate(cycle=cycle, config_path=config_path, env=env)
                if cycle:
                    notify(cycle=cycle, when=str(args.notify_when or "").strip(), env=env)
            if not cycle:
                continue
            if server:
                token = os.environ.get("TESTO_INPUT_SERVER_TOKEN", "").strip()
                if ingest:
                    reports, stage = results, ""
                else:
                    reports = stage_result_dirs(
                        workspace=workspace, config_path=config_path, cycle=cycle
                    )
                    stage = cycle_stage_name(reports)
                if reports or ingest:
                    upload_to_server(
                        cycle=cycle,
                        reports=reports,
                        stage=stage,
                        server=server,
                        token=token,
                        env=env,
                    )
                else:
                    _warn(f"found no results of {cycle} to upload to {server}")

    return int(proc.returncode)


def _prepare_history_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Decide where the action keeps run history.")
    parser.add_argument("--history", default="auto")
    parser.add_argument("--config-path", default="")
    args = parser.parse_args(argv)
    outputs = prepare_history(
        history=str(args.history),
        config_path=str(args.config_path or "").strip(),
        env=dict(os.environ),
    )
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        _write_github_outputs(Path(github_output), values=outputs)
    if outputs["cache"] == "true":
        print(f"Run history: {outputs['dir']} (kept between runs in the Actions cache)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
