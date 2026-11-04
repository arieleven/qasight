"""Stop the running stages when ``qasight`` is told to stop (P3/P4 in docs/engine-cli-review.md).

Each host stage runs in its own process group (see
:mod:`qasight.engine.backends`), so a timeout kills the whole tree, not just the
direct child. The flip side is that Ctrl-C in a terminal no longer reaches the
stage by itself, so while :func:`run_plan` runs on the main thread it installs
handlers for SIGINT and SIGTERM (what CI cancel buttons send) that:

1. first signal: terminate every running stage's group and stop starting new stages;
   the run is still recorded, as interrupted, and ``qasight`` exits 130 (SIGINT) or
   143 (SIGTERM);
2. second signal: kill the groups outright;
3. third: raise :class:`KeyboardInterrupt`, as before.

Stages started off the main thread (the API's worker pool) are killed at interpreter
exit, so stopping the server never leaves test processes behind.

Each API run also gets its own :class:`StopScope` (entered with :func:`scoped`), so
the Stop button stops that run's stages and no other run's. Everything outside a
scope (the CLI) shares one process-wide scope, which the signal handlers use.
"""

from __future__ import annotations

import atexit
import contextvars
import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Any

logger = logging.getLogger(__name__)


class StopScope:
    """One run's stop switch: the stage processes it runs and why it was told to stop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[Any] = set()
        self.signum: int | None = None
        # Who asked, when it wasn't a signal ("a stop request"); shown instead of the signal.
        self.reason: str | None = None
        self.count = 0

    def add(self, proc: Any) -> None:
        with self._lock:
            self._running.add(proc)

    def discard(self, proc: Any) -> None:
        with self._lock:
            self._running.discard(proc)

    def request(self, signum: int = signal.SIGTERM, *, reason: str | None = None) -> int:
        """Ask the run to stop: terminate its stages the first time, kill them after.

        Returns how many times it has been asked.
        """
        with self._lock:
            self.count += 1
            if self.signum is None:
                self.signum = signum
                self.reason = reason
            count = self.count
        self.stop_all(force=count >= 2)
        return count

    def stop_all(self, *, force: bool) -> None:
        with self._lock:
            procs = list(self._running)
        for proc in procs:
            try:
                proc.kill() if force else proc.terminate()
            except (OSError, ValueError):
                logger.debug("could not stop stage process %r", proc, exc_info=True)

    def reset(self) -> None:
        with self._lock:
            self.signum = None
            self.reason = None
            self.count = 0


# The CLI's scope (and anything else that runs outside `scoped`).
_process_scope = StopScope()
_current_scope: contextvars.ContextVar[StopScope | None] = contextvars.ContextVar(
    "testo_stop_scope", default=None
)
# Every stage process of every scope, so interpreter exit can kill them all.
_all_lock = threading.Lock()
_all_running: set[Any] = set()


def current() -> StopScope:
    """The scope of the run on this thread (copied into stage threads by the orchestrator)."""
    return _current_scope.get() or _process_scope


@contextmanager
def scoped(scope: StopScope) -> Iterator[StopScope]:
    """Run the block, and every stage it starts, under ``scope``."""
    token = _current_scope.set(scope)
    try:
        yield scope
    finally:
        _current_scope.reset(token)


def register(proc: Any) -> None:
    """Track a running stage process (anything with ``terminate()`` and ``kill()``)."""
    current().add(proc)
    with _all_lock:
        _all_running.add(proc)


def unregister(proc: Any) -> None:
    current().discard(proc)
    with _all_lock:
        _all_running.discard(proc)


def requested() -> int | None:
    """The signal that asked this run to stop, or None."""
    return current().signum


def signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return f"signal {signum}"


def stop_reason() -> str | None:
    """What stopped this run, in words ("SIGINT", "a stop request"), or None."""
    scope = current()
    if scope.signum is None:
        return None
    return scope.reason or signal_name(scope.signum)


def request_stop(signum: int) -> None:
    """What the signal handler does; callable directly (tests, other front ends)."""
    if current().request(signum) >= 3:
        raise KeyboardInterrupt


def _handler(signum: int, _frame: FrameType | None) -> None:
    request_stop(signum)


@contextmanager
def stop_on_signals() -> Iterator[None]:
    """Install the SIGINT/SIGTERM handlers for the block (main thread only)."""
    # A run's own scope is new, and a stop asked for before its first stage must stand.
    if _current_scope.get() is None:
        _process_scope.reset()
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous: dict[signal.Signals, Any] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):  # not allowed here (embedded interpreter)
            continue
    try:
        yield
    finally:
        for sig, old in previous.items():
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):
                pass


@atexit.register
def _kill_leftovers() -> None:
    with _all_lock:
        procs = list(_all_running)
    for proc in procs:
        try:
            proc.kill()
        except (OSError, ValueError):
            logger.debug("could not stop stage process %r", proc, exc_info=True)
