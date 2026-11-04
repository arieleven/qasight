"""Is a newer qasight out? For ``qasight doctor`` and the web UI's sidebar.

Asks PyPI's JSON API (``https://pypi.org/pypi/qasight/json``) at most once
a day per machine and caches the answer, a failure included, in
``~/.cache/qasight/update-check.json``, so a machine without internet
waits for the timeout once a day, not on every call. Never in CI (``CI`` or
``GITHUB_ACTIONS`` set) and never with ``TESTO_NO_UPDATE_CHECK=1``; then
nothing is sent anywhere. The request carries no data about the project.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

PYPI_URL: Final[str] = "https://pypi.org/pypi/qasight/json"
OFF_ENV: Final[str] = "TESTO_NO_UPDATE_CHECK"
CACHE_SECONDS: Final[int] = 24 * 3600
_CI_ENVS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "CIRCLECI", "BUILDKITE")


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str | None

    @property
    def available(self) -> bool:
        return self.latest is not None and is_newer(self.latest, self.current)

    def notice(self) -> str | None:
        if not self.available:
            return None
        return f"qasight {self.latest} is out (you have {self.current}): pip install -U qasight"


def current_version() -> str:
    from qasight import __version__

    return str(__version__)


def enabled(env: dict[str, str] | None = None) -> bool:
    env = dict(os.environ if env is None else env)
    if env.get(OFF_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return False
    return not any(env.get(name, "").strip() for name in _CI_ENVS)


def _key(version: str) -> tuple[int, ...] | None:
    """``1.6.0`` -> (1, 6, 0); ``None`` for pre-releases and anything else."""
    parts = version.strip().split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def is_newer(candidate: str, current: str) -> bool:
    new, old = _key(candidate), _key(current)
    if new is None:
        return False
    if old is None:
        # A pre-release or dev build (1.6.0rc1, 1.6.0.dev3): compare its release part.
        # The final release of the same line is newer than its pre-release.
        match = re.match(r"\d+(?:\.\d+)*", current.strip())
        base = _key(match.group(0).rstrip(".")) if match else None
        return base is not None and new >= base
    return new > old


def cache_path() -> Path:
    return Path.home() / ".cache" / "qasight" / "update-check.json"


def _fetch(timeout: float) -> str | None:
    request = urllib.request.Request(PYPI_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        data = json.loads(resp.read())
    version = str((data.get("info") or {}).get("version") or "")
    return version or None


def latest_version(*, timeout: float = 3.0, now: float | None = None) -> str | None:
    """PyPI's newest qasight, from a day-old cache when there is one; ``None`` if unknown."""
    now = time.time() if now is None else now
    path = cache_path()
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if now - float(cached.get("checked_at", 0)) < CACHE_SECONDS:
            return cached.get("latest") or None
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    latest: str | None
    try:
        latest = _fetch(timeout)
    except Exception as exc:  # noqa: BLE001 - offline, proxy, PyPI down: no notice, no error
        logger.debug("update check failed: %s", exc)
        latest = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checked_at": now, "latest": latest}), encoding="utf-8")
    except OSError:
        pass
    return latest


def check(*, timeout: float = 3.0) -> UpdateInfo:
    """The running version and, unless the check is off, the newest one on PyPI."""
    current = current_version()
    return UpdateInfo(
        current=current, latest=latest_version(timeout=timeout) if enabled() else None
    )
