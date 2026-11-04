"""Slow down password guessing on ``/login`` (``TESTO_AUTH=accounts``).

Failed sign-ins are counted per username and per client address over a sliding
window. Once either count reaches its limit, further attempts are refused with
``429`` until the oldest failure in the window expires, without checking the
password (so a locked-out guess costs no ``scrypt`` work). A successful sign-in
clears that username's count.

The counts live in the server process, like the run registry, so a restart
clears them. Behind a reverse proxy, set ``FORWARDED_ALLOW_IPS`` so the client
address is the real one and not the proxy's.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable

WINDOW_S = 15 * 60
MAX_FAILURES_PER_NAME_ENV = "TESTO_LOGIN_MAX_FAILURES"
DEFAULT_MAX_FAILURES_PER_NAME = 5
# One address trying many names (password spraying) gets a larger allowance.
FAILURES_PER_ADDRESS_FACTOR = 4
# Bound memory however many names are tried: the oldest keys are dropped first.
MAX_TRACKED_KEYS = 10_000


def _max_failures_from_env() -> int:
    raw = os.environ.get(MAX_FAILURES_PER_NAME_ENV, "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_FAILURES_PER_NAME
    except ValueError:
        return DEFAULT_MAX_FAILURES_PER_NAME


class LoginThrottle:
    def __init__(
        self,
        *,
        max_failures: int | None = None,
        window_s: float = WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures or _max_failures_from_env()
        self.window_s = window_s
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _limits(self, username: str, address: str) -> list[tuple[str, int]]:
        return [
            (f"name:{username.strip().lower()}", self.max_failures),
            (f"addr:{address}", self.max_failures * FAILURES_PER_ADDRESS_FACTOR),
        ]

    def _recent(self, key: str, now: float) -> deque[float]:
        times = self._failures.get(key, deque())
        while times and now - times[0] >= self.window_s:
            times.popleft()
        return times

    def retry_after(self, username: str, address: str) -> int:
        """Seconds until another attempt is allowed, or 0 if it is allowed now."""
        now = self._clock()
        wait = 0.0
        with self._lock:
            for key, limit in self._limits(username, address):
                times = self._recent(key, now)
                if len(times) >= limit:
                    wait = max(wait, self.window_s - (now - times[-limit]))
        return int(wait) + 1 if wait > 0 else 0

    def failed(self, username: str, address: str) -> None:
        now = self._clock()
        with self._lock:
            for key, _limit in self._limits(username, address):
                times = self._recent(key, now)
                times.append(now)
                self._failures.pop(key, None)  # re-insert: dict order is the eviction order
                self._failures[key] = times
            while len(self._failures) > MAX_TRACKED_KEYS:
                self._failures.pop(next(iter(self._failures)))

    def succeeded(self, username: str) -> None:
        with self._lock:
            self._failures.pop(f"name:{username.strip().lower()}", None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle()
