"""Client side of live results: send each finished test to a team server while the
session runs (``POST /api/v1/live-runs``), so the dashboard shows progress and the first
failures before the report is uploaded.

Best effort by design: the first error (server down, old server without the route,
refused token) turns live results off for the rest of the session, and nothing here
ever fails the test run. The report upload at the end records the run as usual.
"""

from __future__ import annotations

import time
from typing import Any

LIVE_TIMEOUT_S = 5.0
FLUSH_EVERY_S = 2.0
FLUSH_AT = 50


class LiveReporter:
    def __init__(self, *, server: str, token: str | None, session: Any = None) -> None:
        import requests

        self._base = f"{server.rstrip('/')}/api/v1/live-runs"
        self._http = session if session is not None else requests.Session()
        if token:
            self._http.headers["Authorization"] = f"Bearer {token}"
        self._id: str | None = None
        self._buffer: list[dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self.disabled = False
        self.error: str | None = None

    @property
    def started(self) -> bool:
        return self._id is not None

    def _post(self, url: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            resp = self._http.post(url, json=payload or {}, timeout=LIVE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - any failure turns live results off
            self._stop(f"could not reach {url}: {exc}")
            return None
        if resp.status_code >= 300:
            self._stop(f"{url} answered {resp.status_code}")
            return None
        return resp

    def _stop(self, reason: str) -> None:
        self.disabled = True
        self.error = reason
        self._buffer.clear()

    def start(
        self,
        cycle: str,
        *,
        total: int | None = None,
        branch: str | None = None,
        commit: str | None = None,
    ) -> None:
        if self.disabled or self.started:
            return
        payload = {"cycle": cycle, "total": total, "branch": branch, "commit": commit}
        resp = self._post(self._base, {k: v for k, v in payload.items() if v is not None})
        if resp is None:
            return
        try:
            self._id = str(resp.json()["id"])
        except (ValueError, KeyError, TypeError):
            self._stop("the server's answer had no live session id")

    def add(self, test: str, outcome: str, message: str | None = None) -> None:
        if self.disabled or not self.started:
            return
        item: dict[str, Any] = {"test": test[:1000], "outcome": outcome}
        if message:
            item["message"] = message[:5000]
        self._buffer.append(item)
        if len(self._buffer) >= FLUSH_AT or time.monotonic() - self._last_flush >= FLUSH_EVERY_S:
            self.flush()

    def flush(self) -> None:
        if self.disabled or not self.started or not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        self._last_flush = time.monotonic()
        self._post(f"{self._base}/{self._id}/results", {"results": batch})

    def finish(self) -> None:
        if self.disabled or not self.started:
            return
        self.flush()
        if not self.disabled:
            self._post(f"{self._base}/{self._id}/finish")
