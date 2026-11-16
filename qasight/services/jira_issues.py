"""File a failing test in Jira Cloud (or Jira Data Center) from the run page, and link it.

Set on the server:

- ``TESTO_JIRA_URL``: ``https://your-team.atlassian.net``
- ``TESTO_JIRA_EMAIL`` and ``TESTO_JIRA_TOKEN``: an account and its API token
  (Jira Cloud). For Jira Data Center leave the email empty and set a personal
  access token; it is sent as a bearer token.
- ``TESTO_JIRA_PROJECT``: the project key issues go to (``SHOP``).
- ``TESTO_JIRA_ISSUE_TYPE``: default ``Bug``.

``POST /api/v1/jira/issues`` creates the issue with the test's name, cycle, failure
message and a link back to the run, then marks the failure's cause as a product bug
with the issue key and link as the note (:mod:`qasight.services.triage`), so later
failures with the same message point to the same issue. Nothing is sent to Jira
unless someone presses the button (or calls the route).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Final

import requests

URL_ENV: Final[str] = "TESTO_JIRA_URL"
EMAIL_ENV: Final[str] = "TESTO_JIRA_EMAIL"
TOKEN_ENV: Final[str] = "TESTO_JIRA_TOKEN"
PROJECT_ENV: Final[str] = "TESTO_JIRA_PROJECT"
ISSUE_TYPE_ENV: Final[str] = "TESTO_JIRA_ISSUE_TYPE"
_SUMMARY_LIMIT = 250
_MESSAGE_LIMIT = 3000


class JiraError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class JiraConfig:
    url: str
    email: str
    token: str
    project: str
    issue_type: str = "Bug"

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token and self.project)


def config_from_env() -> JiraConfig:
    return JiraConfig(
        url=os.getenv(URL_ENV, "").strip().rstrip("/"),
        email=os.getenv(EMAIL_ENV, "").strip(),
        token=os.getenv(TOKEN_ENV, "").strip(),
        project=os.getenv(PROJECT_ENV, "").strip(),
        issue_type=os.getenv(ISSUE_TYPE_ENV, "").strip() or "Bug",
    )


@dataclass(frozen=True)
class JiraIssue:
    key: str
    url: str


def _paragraph(text: str, *, code: bool = False) -> dict[str, Any]:
    if code:
        return {"type": "codeBlock", "content": [{"type": "text", "text": text}]}
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def description(*, cycle: str, test: str, message: str, run_url: str | None) -> dict[str, Any]:
    """The issue body in Atlassian Document Format (what Jira Cloud's API v3 takes)."""
    content = [
        _paragraph(f"Test: {test}"),
        _paragraph(f"Cycle: {cycle}"),
    ]
    if message:
        content.append(_paragraph(message[:_MESSAGE_LIMIT], code=True))
    if run_url:
        content.append(
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Run in QASight: "},
                    {
                        "type": "text",
                        "text": run_url,
                        "marks": [{"type": "link", "attrs": {"href": run_url}}],
                    },
                ],
            }
        )
    return {"type": "doc", "version": 1, "content": content}


class JiraClient:
    def __init__(self, config: JiraConfig, *, session: requests.Session | None = None) -> None:
        self.config = config
        self._session = session or requests.Session()
        if config.email:
            self._session.auth = (config.email, config.token)
        else:
            self._session.headers["Authorization"] = f"Bearer {config.token}"
        self._session.headers["Accept"] = "application/json"

    def create_issue(
        self, *, summary: str, cycle: str, test: str, message: str, run_url: str | None
    ) -> JiraIssue:
        cfg = self.config
        body: dict[str, Any] = {
            "fields": {
                "project": {"key": cfg.project},
                "issuetype": {"name": cfg.issue_type},
                "summary": summary[:_SUMMARY_LIMIT],
                "description": description(
                    cycle=cycle, test=test, message=message, run_url=run_url
                ),
                "labels": ["qasight"],
            }
        }
        try:
            resp = self._session.post(f"{cfg.url}/rest/api/3/issue", json=body, timeout=30)
        except requests.RequestException as exc:
            raise JiraError(f"could not reach Jira: {exc}") from exc
        if resp.status_code in (401, 403):
            raise JiraError("Jira refused the account or token", status=resp.status_code)
        if resp.status_code >= 400:
            try:
                errors = resp.json()
                detail = "; ".join(
                    [*errors.get("errorMessages", []), *errors.get("errors", {}).values()]
                )
            except ValueError:
                detail = resp.text[:200]
            raise JiraError(
                f"Jira answered HTTP {resp.status_code}: {detail}", status=resp.status_code
            )
        key = str(resp.json().get("key") or "")
        if not key:
            raise JiraError("Jira created no issue key")
        return JiraIssue(key=key, url=f"{cfg.url}/browse/{key}")


def summary_for(test: str, message: str) -> str:
    """``test_checkout fails: AssertionError: total 3 != 4``, cut to Jira's limit."""
    first = (message or "").strip().split("\n", 1)[0]
    text = f"{test} fails: {first}" if first else f"{test} fails"
    return text[:_SUMMARY_LIMIT]
