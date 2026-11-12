"""A small read-side cache for the heavy views over run history (manager report, attention, project health).

Each entry is keyed on the view's name and arguments plus :func:`history_fingerprint`,
so a new, finished, re-labelled-by-status or deleted run is seen on the next request.
Entries also expire after ``ttl_s`` seconds, which bounds how long anything the
fingerprint can't see (``qasight.yaml``, quarantine, the clock behind "last week")
stays stale.

Concurrent requests for the same missing entry compute it once: the others wait for
that result instead of each rebuilding the view (what made the API crawl under load,
see ``docs/Specs & ADRs/External Audit Tools - 2026-11-11.md``).

The cache is per process. Set ``TESTO_READ_CACHE_S=0`` to turn it off.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

from qasight.repository.db import get_repository
from qasight.repository.db_config import get_engine

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_TTL_S = 60.0
MAX_ENTRIES = 64

_lock = threading.Lock()
_entries: OrderedDict[tuple[Hashable, ...], tuple[float, Any]] = OrderedDict()
_inflight: dict[tuple[Hashable, ...], threading.Lock] = {}


def ttl_s() -> float:
    """Seconds an entry lives: ``TESTO_READ_CACHE_S`` or 60; 0 turns caching off."""
    raw = os.environ.get("TESTO_READ_CACHE_S", "").strip()
    if not raw:
        return DEFAULT_TTL_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_TTL_S


def history_fingerprint() -> tuple[Any, ...] | None:
    """The run store's fingerprint, or ``None`` when it can't be read (then nothing is cached)."""
    try:
        # The database URL too: one process can point at another database (tests, `--db`).
        return (str(get_engine().url), get_repository().history_fingerprint())
    except Exception:  # noqa: BLE001 - a DB error here must not break the view; it just isn't cached
        logger.debug("memo: could not read the history fingerprint", exc_info=True)
        return None


def clear() -> None:
    """Drop every entry (tests, and after a restore from backup)."""
    with _lock:
        _entries.clear()


def cached(name: str, args: Hashable, compute: Callable[[], T]) -> T:
    """``compute()``'s result for ``(name, args)`` at the current run history, reused while fresh."""
    life = ttl_s()
    fingerprint = history_fingerprint() if life > 0 else None
    if fingerprint is None:
        return compute()
    key: tuple[Hashable, ...] = (name, args, fingerprint)
    now = time.monotonic()
    with _lock:
        hit = _entries.get(key)
        if hit is not None and now - hit[0] < life:
            _entries.move_to_end(key)
            return hit[1]  # type: ignore[no-any-return]
        gate = _inflight.setdefault(key, threading.Lock())
    with gate:
        # Another request may have filled it while this one waited at the gate.
        with _lock:
            hit = _entries.get(key)
            if hit is not None and time.monotonic() - hit[0] < life:
                return hit[1]  # type: ignore[no-any-return]
        try:
            value = compute()
        except BaseException:
            with _lock:
                _inflight.pop(key, None)
            raise
        with _lock:
            _inflight.pop(key, None)
            _entries[key] = (time.monotonic(), value)
            _entries.move_to_end(key)
            # Older fingerprints of the same view are dead weight once a newer one exists.
            for old in [k for k in _entries if k[:2] == key[:2] and k != key]:
                del _entries[old]
            while len(_entries) > MAX_ENTRIES:
                _entries.popitem(last=False)
        return value
