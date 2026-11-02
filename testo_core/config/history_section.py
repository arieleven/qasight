"""Top-level ``history:`` section of ``testosterone.yaml``: how long run history is kept.

.. code-block:: yaml

    history:
      retention:
        days: 90            # runs that started more than 90 days ago go
        runs_per_cycle: 200 # and each cycle keeps at most its newest 200 runs
      max_file_mb: 100      # files bigger than this aren't kept in a run's history copy

Either limit alone is fine; a run goes when it breaks any of them, and so does a
report archive (``testo report list``) by the same rules. Without a ``history:``
section nothing is ever deleted. On a server without a ``testosterone.yaml`` the
same limits come from ``TESTO_RETENTION_DAYS``, ``TESTO_RETENTION_RUNS_PER_CYCLE``
and ``TESTO_HISTORY_MAX_FILE_MB``, which also override the file
(:func:`with_env_overrides`). The pruning itself is
:func:`testo_core.history.retention.prune_history`, run by ``testo clean --history``
and by the API on a timer.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from testo_core.config.errors import ConfigValidationError

# How often the API server applies history.retention. Pruning is cheap when nothing
# expired, and a run only ever expires by getting older, so hours are plenty.
SERVER_PRUNE_INTERVAL_S = 6 * 60 * 60

_RETENTION_KEYS = frozenset({"days", "runs_per_cycle"})


@dataclass(frozen=True)
class HistoryRetention:
    """Limits on stored runs; ``None`` means no limit of that kind."""

    days: int | None = None
    runs_per_cycle: int | None = None

    @property
    def enabled(self) -> bool:
        return self.days is not None or self.runs_per_cycle is not None


@dataclass(frozen=True)
class HistoryConfig:
    retention: HistoryRetention = HistoryRetention()
    # Files bigger than this many MB are left out of a run's history copy and its
    # report archive (videos, traces); None keeps every file.
    max_file_mb: int | None = None


RETENTION_DAYS_ENV = "TESTO_RETENTION_DAYS"
RETENTION_RUNS_ENV = "TESTO_RETENTION_RUNS_PER_CYCLE"
MAX_FILE_MB_ENV = "TESTO_HISTORY_MAX_FILE_MB"


def _env_int(env: Mapping[str, str], name: str) -> tuple[bool, int | None]:
    """``(set, value)``: unset or empty keeps the file's value; ``0`` switches the limit off."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return False, None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigValidationError(f"{name} must be a whole number, not {raw!r}.") from exc
    if value < 0:
        raise ConfigValidationError(f"{name} must be 0 (no limit) or more, not {raw!r}.")
    return True, value or None


def with_env_overrides(
    history: HistoryConfig, env: Mapping[str, str] | None = None
) -> HistoryConfig:
    """``history`` with the ``TESTO_RETENTION_*`` and ``TESTO_HISTORY_MAX_FILE_MB``
    variables applied: set ones win over the file, ``0`` means no limit."""
    env = os.environ if env is None else env
    days_set, days = _env_int(env, RETENTION_DAYS_ENV)
    runs_set, runs = _env_int(env, RETENTION_RUNS_ENV)
    max_set, max_mb = _env_int(env, MAX_FILE_MB_ENV)
    retention = history.retention
    if days_set:
        retention = replace(retention, days=days)
    if runs_set:
        retention = replace(retention, runs_per_cycle=runs)
    result = replace(history, retention=retention)
    if max_set:
        result = replace(result, max_file_mb=max_mb)
    return result


def _positive_int(value: Any, *, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigValidationError(
            f"'history.retention.{key}' must be a whole number of 1 or more."
        )
    return value


def parse_history(raw: Any) -> HistoryConfig:
    """``history:`` (``None`` gives the defaults: keep everything)."""
    if raw is None:
        return HistoryConfig()
    if not isinstance(raw, dict):
        raise ConfigValidationError("'history:' must be a mapping.")
    unknown = set(raw) - {"retention", "max_file_mb"}
    if unknown:
        raise ConfigValidationError(f"'history:' has unknown keys: {', '.join(sorted(unknown))}.")
    max_file_mb = raw.get("max_file_mb")
    if max_file_mb is not None and (
        isinstance(max_file_mb, bool) or not isinstance(max_file_mb, int) or max_file_mb < 1
    ):
        raise ConfigValidationError("'history.max_file_mb' must be a whole number of 1 or more.")
    retention_raw = raw.get("retention")
    if retention_raw is None:
        return HistoryConfig(max_file_mb=max_file_mb)
    if not isinstance(retention_raw, dict):
        raise ConfigValidationError("'history.retention' must be a mapping.")
    unknown = set(retention_raw) - _RETENTION_KEYS
    if unknown:
        raise ConfigValidationError(
            f"'history.retention' has unknown keys: {', '.join(sorted(unknown))}."
        )
    return HistoryConfig(
        retention=HistoryRetention(
            days=_positive_int(retention_raw.get("days"), key="days"),
            runs_per_cycle=_positive_int(retention_raw.get("runs_per_cycle"), key="runs_per_cycle"),
        ),
        max_file_mb=max_file_mb,
    )


def max_history_file_bytes() -> int | None:
    """``history.max_file_mb`` (or ``TESTO_HISTORY_MAX_FILE_MB``) in bytes; None keeps all.

    Read where a run is recorded (the history copy, the report archive). A config
    that fails to load, or a bad variable, counts as no limit: recording a run must
    never fail on this.
    """
    from testo_core.config.errors import ConfigError
    from testo_core.config.loader import discover_and_load

    try:
        history = discover_and_load().history
    except ConfigError:
        history = HistoryConfig()
    try:
        mb = with_env_overrides(history).max_file_mb
    except ConfigError:
        return None
    return mb * 1024 * 1024 if mb else None
