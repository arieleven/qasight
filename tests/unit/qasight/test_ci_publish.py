"""``qasight ci publish``: one after-run step for GitHub, GitLab and any other CI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qasight.services import ci_publish, run_summary_markdown
from qasight.services.ci_publish import PublishOptions


def test_context_finds_the_review_and_a_link(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7}}), encoding="utf-8")
    gh = ci_publish.context(
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_RUN_ID": "9",
        }
    )
    assert (gh.provider, gh.review, gh.run_url) == (
        "github",
        7,
        "https://github.com/o/r/actions/runs/9",
    )
    gl = ci_publish.context(
        {"GITLAB_CI": "true", "CI_MERGE_REQUEST_IID": "12", "CI_JOB_URL": "https://gl/j/1"}
    )
    assert (gl.provider, gl.review, gl.run_url) == ("gitlab", 12, "https://gl/j/1")
    jenkins = ci_publish.context({"JENKINS_URL": "x", "BUILD_URL": "https://ci/b/3"})
    assert (jenkins.provider, jenkins.review, jenkins.run_url) == ("other", None, "https://ci/b/3")


def _fake_api(monkeypatch: pytest.MonkeyPatch, existing: list[dict[str, Any]]) -> list[tuple]:
    calls: list[tuple] = []

    def fake_call(method: str, url: str, headers: dict[str, str], body: Any) -> Any:
        calls.append((method, url, headers, body))
        return existing if method == "GET" else {}

    monkeypatch.setattr(ci_publish, "_call", fake_call)
    return calls


def test_gitlab_note_is_edited_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_api(monkeypatch, [{"id": 5, "body": "<!-- testo-summary:api -->\nold"}])
    env = {"CI_API_V4_URL": "https://gl/api/v4", "CI_PROJECT_ID": "group/proj"}
    done = ci_publish.upsert_gitlab_note(
        markdown="<!-- testo-summary:api -->\nnew", iid=3, token="tok", env=env
    )
    assert done == "updated"
    method, url, headers, body = calls[-1]
    assert (method, url) == (
        "PUT",
        "https://gl/api/v4/projects/group%2Fproj/merge_requests/3/notes/5",
    )
    assert headers["PRIVATE-TOKEN"] == "tok" and body == {"body": "<!-- testo-summary:api -->\nnew"}


def test_github_comment_is_posted_when_none_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_api(monkeypatch, [{"id": 1, "body": "someone else"}])
    done = ci_publish.upsert_github_comment(
        markdown="<!-- m -->\nx", number=7, token="t", env={"GITHUB_REPOSITORY": "o/r"}
    )
    assert done == "created"
    assert calls[-1][:2] == ("POST", "https://api.github.com/repos/o/r/issues/7/comments")


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    run = SimpleNamespace(returncode=1, run_id="r1", cycle="api")
    monkeypatch.setattr(
        run_summary_markdown, "load_run_summary", lambda **_: SimpleNamespace(run=run)
    )
    monkeypatch.setattr(
        run_summary_markdown,
        "render_markdown",
        lambda s, run_url=None: f"<!-- m -->\nsummary {run_url}\n",
    )


@pytest.mark.usefixtures("recorded")
def test_gitlab_merge_request_gets_the_comment_and_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_api(monkeypatch, [])
    out: list[str] = []
    env = {
        "GITLAB_CI": "true",
        "CI_MERGE_REQUEST_IID": "4",
        "CI_PROJECT_ID": "9",
        "CI_JOB_URL": "https://gl/j",
        "TESTO_GITLAB_TOKEN": "tok",
    }
    opts = PublishOptions(out=tmp_path / "summary.md", notify_when=None)
    assert ci_publish.publish(cycle="api", env=env, opts=opts, echo=out.append, warn=out.append)
    assert (tmp_path / "summary.md").read_text(
        encoding="utf-8"
    ) == "<!-- m -->\nsummary https://gl/j\n"
    assert calls[-1][0] == "POST" and calls[-1][1].endswith("/projects/9/merge_requests/4/notes")
    assert out == ["merge request !4 comment created"]


@pytest.mark.usefixtures("recorded")
def test_a_missing_token_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    warnings: list[str] = []
    env = {"GITLAB_CI": "true", "CI_MERGE_REQUEST_IID": "4", "CI_PROJECT_ID": "9"}
    opts = PublishOptions(notify_when=None)
    assert ci_publish.publish(cycle="api", env=env, opts=opts, echo=print, warn=warnings.append)
    assert len(warnings) == 1 and "TESTO_GITLAB_TOKEN" in warnings[0]


@pytest.mark.usefixtures("recorded")
def test_github_job_summary_and_fork_comment_file(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7}}), encoding="utf-8")
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step.md"),
    }
    opts = PublishOptions(annotations=False, notify_when=None, comment_dir=tmp_path / "comments")
    assert ci_publish.publish(cycle="api", env=env, opts=opts, echo=print, warn=print)
    assert "summary" in (tmp_path / "step.md").read_text(encoding="utf-8")
    assert (tmp_path / "comments" / "api.md").read_text(encoding="utf-8").startswith("<!-- m -->")


def test_no_recorded_run_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_summary_markdown, "load_run_summary", lambda **_: None)
    warnings: list[str] = []
    assert not ci_publish.publish(cycle="x", env={}, opts=PublishOptions(), warn=warnings.append)
    assert warnings == ["no recorded run of 'x' to publish"]


@pytest.mark.usefixtures("recorded")
def test_a_channel_qasight_yaml_alerts_through_is_not_sent_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.services import run_alerts

    config = tmp_path / "qasight.yaml"
    config.write_text(
        "version: 1\nreporters:\n  - type: slack\ncycles:\n  api:\n    stages:\n"
        "      - name: unit\n        equipment: pytest\n",
        encoding="utf-8",
    )
    posted: list[str] = []
    monkeypatch.setattr(run_alerts, "load_alert", lambda **_: object())
    monkeypatch.setattr(run_alerts, "should_send", lambda alert, when: True)
    monkeypatch.setattr(run_alerts, "build_payload", lambda channel, *a, **k: {"c": channel})
    monkeypatch.setattr(run_alerts, "post", lambda url, payload: posted.append(payload["c"]))
    env = {
        "TESTO_SLACK_WEBHOOK_URL": "https://hooks/slack",
        "TESTO_TEAMS_WEBHOOK_URL": "https://hooks/teams",
    }
    opts = PublishOptions(comment=False, config_path=config)
    assert ci_publish.publish(cycle="api", env=env, opts=opts, echo=print, warn=print)
    assert posted == ["teams"]
