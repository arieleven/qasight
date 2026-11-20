"""Record GitLab pipelines' test reports (``qasight ingest --from-gitlab``).

GitLab already collects the JUnit XML of every job that declares
``artifacts: reports: junit:`` and serves it, parsed, as each pipeline's test
report (``GET /projects/:id/pipelines/:id/test_report``). Reading that needs no
change in any job: one scheduled command records a whole monorepo, and its
first run fills the history from past pipelines.

Each finished pipeline (and the child pipelines it triggered) is turned back
into JUnit XML, one file per test suite (GitLab names a suite after its job),
and recorded like ``qasight ingest`` with the pipeline's commit, branch and id,
dated when it ran. With no ``--cycle``, each job becomes its own cycle (the
job's name, as the GitLab include names it); with ``--cycle`` the whole
pipeline is one run. A pipeline already recorded for a cycle (same pipeline id,
from an earlier pass or from the include in a job) is skipped, so running it
again only adds what is new.

The token (``TESTO_GITLAB_TOKEN`` or ``GITLAB_TOKEN``) needs ``read_api``.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from qasight.services.github_backfill import backdate_run, recorded_run_ids

TOKEN_ENVS = ("TESTO_GITLAB_TOKEN", "GITLAB_TOKEN")
_FINISHED = ("success", "failed")
_TIMEOUT_S = 60.0
_MAX_CHILD_DEPTH = 3
_PAGE = 100
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def escape(text: str) -> str:
    """*text* safe as XML character data."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def quoteattr(text: str) -> str:
    """*text* as a double-quoted XML attribute value."""
    return '"' + escape(text).replace('"', "&quot;").replace("\n", "&#10;") + '"'


class GitLabError(RuntimeError):
    """GitLab could not be reached, or refused the request; the message says why."""


@dataclass(frozen=True)
class Pipeline:
    pipeline_id: str
    ref: str
    sha: str
    created_at: str
    updated_at: str
    status: str
    web_url: str = ""


@dataclass
class GitLabBackfillReport:
    recorded: list[tuple[Pipeline, str]] = field(default_factory=list)  # (pipeline, cycle)
    already: list[tuple[Pipeline, str]] = field(default_factory=list)
    no_results: list[Pipeline] = field(default_factory=list)
    failed: list[tuple[Pipeline, str]] = field(default_factory=list)


def gitlab_url(explicit: str | None, env: Mapping[str, str]) -> str | None:
    """The GitLab base URL: *explicit*, else ``CI_SERVER_URL`` (set in GitLab jobs)."""
    url = (explicit or env.get("CI_SERVER_URL") or env.get("GITLAB_URL") or "").strip()
    return url.rstrip("/") or None


def project_ref(explicit: str | None, env: Mapping[str, str]) -> str | None:
    """The project: *explicit* (``group/name`` or an id), else ``CI_PROJECT_ID``."""
    project = (explicit or env.get("CI_PROJECT_ID") or env.get("CI_PROJECT_PATH") or "").strip()
    return project or None


def token_from(env: Mapping[str, str]) -> str | None:
    return next((env[name].strip() for name in TOKEN_ENVS if env.get(name, "").strip()), None)


def cycle_for_suite(name: str) -> str:
    """A job's name as a cycle name, the way the GitLab include names it."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.").lower()
    return slug or "tests"


class GitLabClient:
    """The few GitLab REST calls this needs (``requests``, so a company CA works through
    ``REQUESTS_CA_BUNDLE`` and a proxy through ``HTTPS_PROXY``)."""

    def __init__(self, base_url: str, project: str, token: str | None) -> None:
        self.api = f"{base_url}/api/v4/projects/{quote(project, safe='')}"
        self.headers = {"PRIVATE-TOKEN": token} if token else {}

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        import requests

        url = f"{self.api}{path}"
        try:
            resp = requests.get(url, params=params, headers=self.headers, timeout=_TIMEOUT_S)
        except requests.RequestException as exc:
            raise GitLabError(f"could not reach {url}: {exc}") from exc
        if resp.status_code in (401, 403):
            raise GitLabError(
                f"GitLab refused the token ({resp.status_code}); set TESTO_GITLAB_TOKEN to a "
                "token with read_api on this project"
            )
        if resp.status_code == 404:
            raise GitLabError(f"{url}: not found (check --gitlab-url and --project)")
        if resp.status_code != 200:
            raise GitLabError(f"{url} answered {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise GitLabError(f"{url} answered with something that is not JSON") from exc

    def pipelines(self, *, limit: int, branch: str | None = None) -> list[Pipeline]:
        """The *limit* newest finished pipelines, oldest first."""
        found: list[Pipeline] = []
        page = 1
        while len(found) < limit:
            params: dict[str, Any] = {
                "per_page": _PAGE,
                "page": page,
                "order_by": "id",
                "sort": "desc",
            }
            if branch:
                params["ref"] = branch
            rows = self._get("/pipelines", params)
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if row.get("status") in _FINISHED:
                    found.append(_pipeline(row))
            if len(rows) < _PAGE:
                break
            page += 1
        return list(reversed(found[:limit]))

    def test_suites(self, pipeline_id: str, *, depth: int = 0) -> list[dict[str, Any]]:
        """The pipeline's test suites, and those of the child pipelines it triggered."""
        report = self._get(f"/pipelines/{pipeline_id}/test_report")
        suites = list(report.get("test_suites") or []) if isinstance(report, dict) else []
        if depth < _MAX_CHILD_DEPTH:
            bridges = self._get(f"/pipelines/{pipeline_id}/bridges", {"per_page": _PAGE})
            for bridge in bridges if isinstance(bridges, list) else []:
                child = bridge.get("downstream_pipeline") or {}
                child_project = child.get("project_id")
                own = bridge.get("pipeline", {}).get("project_id")
                if child.get("id") and (child_project is None or child_project == own):
                    suites += self.test_suites(str(child["id"]), depth=depth + 1)
        return suites


def _pipeline(row: Mapping[str, Any]) -> Pipeline:
    return Pipeline(
        pipeline_id=str(row.get("id")),
        ref=str(row.get("ref") or ""),
        sha=str(row.get("sha") or ""),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or row.get("created_at") or ""),
        status=str(row.get("status") or ""),
        web_url=str(row.get("web_url") or ""),
    )


def _text(value: object) -> str:
    return _INVALID_XML.sub("", str(value or ""))


def suite_junit_xml(suite: Mapping[str, Any]) -> str:
    """One GitLab test suite as a JUnit XML document."""
    cases = list(suite.get("test_cases") or [])
    counts = {"failed": 0, "error": 0, "skipped": 0}
    body: list[str] = []
    for case in cases:
        status = str(case.get("status") or "success")
        counts[status] = counts.get(status, 0) + 1
        attrs = (
            f"classname={quoteattr(_text(case.get('classname')))} "
            f"name={quoteattr(_text(case.get('name')))} "
            f'time="{float(case.get("execution_time") or 0):.3f}"'
        )
        if case.get("file"):
            attrs += f" file={quoteattr(_text(case.get('file')))}"
        inner = ""
        trace = _text(case.get("stack_trace") or case.get("system_output"))
        message = trace.strip().splitlines()[0][:500] if trace.strip() else status
        if status == "failed":
            inner = f"<failure message={quoteattr(message)}>{escape(trace)}</failure>"
        elif status == "error":
            inner = f"<error message={quoteattr(message)}>{escape(trace)}</error>"
        elif status == "skipped":
            inner = "<skipped/>"
        if case.get("system_output") and status not in ("failed", "error"):
            inner += f"<system-out>{escape(_text(case.get('system_output')))}</system-out>"
        body.append(f"  <testcase {attrs}>{inner}</testcase>")
    head = (
        f'<testsuite name={quoteattr(_text(suite.get("name")))} tests="{len(cases)}" '
        f'failures="{counts["failed"]}" errors="{counts["error"]}" '
        f'skipped="{counts["skipped"]}" time="{float(suite.get("total_time") or 0):.3f}">'
    )
    return '<?xml version="1.0" encoding="utf-8"?>\n' + "\n".join([head, *body, "</testsuite>"])


@contextlib.contextmanager
def as_gitlab_pipeline(pipeline: Pipeline, *, project: str) -> Iterator[None]:
    """Make the recorded run carry *pipeline*'s CI facts (read from the environment on save)."""
    values = {
        "GITLAB_CI": "true",
        "CI_PIPELINE_ID": pipeline.pipeline_id,
        "CI_COMMIT_SHA": pipeline.sha,
        "CI_COMMIT_REF_NAME": pipeline.ref,
        "CI_PROJECT_PATH": project if not project.isdigit() else "",
        "CI_JOB_ID": "",
        "CI_JOB_STARTED_AT": "",
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "",
        # Several CI systems' variables at once would confuse provenance detection.
        "GITHUB_ACTIONS": "",
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


def backfill(
    *,
    client: GitLabClient,
    project: str,
    limit: int,
    record: Callable[[list[str], Path, str], int],
    cycle: str | None = None,
    branch: str | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> GitLabBackfillReport:
    """Record up to *limit* finished pipelines; *record(reports, folder, cycle)* records one run."""
    report = GitLabBackfillReport()
    known: dict[str, set[str]] = {}

    def seen(name: str) -> set[str]:
        if name not in known:
            known[name] = recorded_run_ids(name)
        return known[name]

    for pipeline in client.pipelines(limit=limit, branch=branch):
        try:
            suites = [s for s in client.test_suites(pipeline.pipeline_id) if s.get("test_cases")]
        except GitLabError as exc:
            report.failed.append((pipeline, str(exc)))
            continue
        if not suites:
            report.no_results.append(pipeline)
            continue
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for suite in suites:
            name = cycle or cycle_for_suite(str(suite.get("name") or "tests"))
            groups.setdefault(name, []).append(suite)
        for name, members in groups.items():
            if pipeline.pipeline_id in seen(name):
                report.already.append((pipeline, name))
                continue
            with tempfile.TemporaryDirectory(prefix="qasight-gitlab-") as tmp:
                folder = Path(tmp)
                reports = []
                for index, member in enumerate(members):
                    path = folder / f"suite-{index + 1}.xml"
                    path.write_text(suite_junit_xml(member), encoding="utf-8")
                    reports.append(path.name)
                progress(
                    f"pipeline {pipeline.pipeline_id} ({pipeline.ref}, "
                    f"{pipeline.created_at[:10]}): {name}"
                )
                with as_gitlab_pipeline(pipeline, project=project):
                    code = record(reports, folder, name)
            if code not in (0, 1):
                report.failed.append((pipeline, f"recording {name} exited {code}"))
                continue
            backdate_run(
                name,
                pipeline.pipeline_id,
                pipeline.created_at,
                pipeline.updated_at,
                source="gitlab",
            )
            seen(name).add(pipeline.pipeline_id)
            report.recorded.append((pipeline, name))
    return report
