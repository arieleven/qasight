"""A small GitHub REST client for issues, on the standard library.

Only what :mod:`qasight.services.flaky_issues` needs: list a repository's
open issues, open one, comment on one and close one. ``urllib.request`` keeps
it free of a new dependency; ``GITHUB_API_URL`` points it at GitHub Enterprise
Server. Every failure, network or HTTP, is a :class:`GitHubError` whose message
never contains the token.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

DEFAULT_API_URL = "https://api.github.com"
_TIMEOUT_S = 10.0
_PER_PAGE = 100
_MAX_PAGES = 10
_USER_AGENT = "qasight-flaky-issues"

# (request, timeout) -> response with .status and .read(); urllib.request.urlopen by default.
Opener = Callable[..., Any]


class GitHubError(RuntimeError):
    """GitHub refused the request or could not be reached."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    """Issues of one ``owner/name`` repository."""

    def __init__(
        self,
        *,
        token: str,
        repository: str,
        api_url: str = DEFAULT_API_URL,
        timeout: float = _TIMEOUT_S,
        opener: Opener | None = None,
    ) -> None:
        self.repository = repository
        self._token = token
        self._base = f"{(api_url or DEFAULT_API_URL).rstrip('/')}/repos/{repository}"
        self._timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(  # noqa: S310 - the URL is https://<GitHub API>/...
            f"{self._base}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
                "User-Agent": _USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with self._open(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read() or b"{}").get("message") or ""
            except (ValueError, AttributeError, OSError):
                pass
            raise GitHubError(
                f"GitHub answered HTTP {exc.code} to {method} {path.split('?')[0]}"
                + (f": {str(detail)[:200]}" if detail else ""),
                status=exc.code,
            ) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise GitHubError(f"GitHub unreachable ({type(exc).__name__})") from None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise GitHubError(f"GitHub sent a response that is not JSON to {method}") from None

    def list_open_issues(self, *, labels: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """Open issues (pull requests left out), with ``labels`` when given; up to 1000."""
        found: list[dict[str, Any]] = []
        query: dict[str, str | int] = {"state": "open", "per_page": _PER_PAGE}
        if labels:
            query["labels"] = ",".join(labels)
        for page in range(1, _MAX_PAGES + 1):
            batch = self._request("GET", f"/issues?{urlencode({**query, 'page': page})}")
            if not isinstance(batch, list):
                break
            found += [i for i in batch if isinstance(i, dict) and "pull_request" not in i]
            if len(batch) < _PER_PAGE:
                break
        return found

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: tuple[str, ...] = (),
        assignees: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = list(labels)
        if assignees:
            payload["assignees"] = list(assignees)
        out = self._request("POST", "/issues", payload)
        return out if isinstance(out, dict) else {}

    def comment(self, number: int, body: str) -> None:
        self._request("POST", f"/issues/{quote(str(number))}/comments", {"body": body})

    def close(self, number: int) -> None:
        self._request(
            "PATCH",
            f"/issues/{quote(str(number))}",
            {"state": "closed", "state_reason": "completed"},
        )
