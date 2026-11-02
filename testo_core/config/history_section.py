"""Top-level ``history:`` section of ``testosterone.yaml``: how long run history is kept.

.. code-block:: yaml

    history:
      retention:
        days: 90            # runs that started more than 90 days ago go
        runs_per_cycle: 200 # and each cycle keeps at most its newest 200 runs

Either limit alone is fine; a run goes when it breaks any of them. Without a
``history:`` section nothing is ever deleted. The pruning itself is
:func:`testo_core.history.retention.prune_history`, run by ``testo clean --history``
and by the API on a timer.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    unknown = set(raw) - {"retention"}
    if unknown:
        raise ConfigValidationError(f"'history:' has unknown keys: {', '.join(sorted(unknown))}.")
    retention_raw = raw.get("retention")
    if retention_raw is None:
        return HistoryConfig()
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
        )
    )
