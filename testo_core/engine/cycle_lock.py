"""One run of a cycle at a time per artifacts folder.

Every run of a cycle writes ``<artifacts>/<cycle>/<stage>/`` and resets it when the
stage starts, so two runs of one cycle at once (two CI jobs on one runner, ``testo
watch`` plus a manual run, the CLI next to the API) wipe each other's results and
mix their logs. :func:`cycle_lock` holds an OS file lock on
``<artifacts>/<cycle>/.lock`` for the whole run; a second run waits for it and says
so. The OS drops the lock when the process exits, so a crashed run never leaves it
held. See ``docs/Specs & ADRs/Engine Fixes - 2026-11-01.md``.

The lock file is also the run's journal: while a run holds it, it says who started
the run and when (:class:`RunMarker`), and it is emptied when the run ends, however
it ends inside Python. A marker still there when the lock is next taken means the
holder died without finishing (``kill -9``, out of memory, the machine or the API
server going down), so the next run, or the API at startup, can record that run
instead of losing it. See ``docs/Specs & ADRs/Lifecycle Fixes - 2026-11-02.md``.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO

from testo_core.reporting.paths import plan_artifacts_dir

LOCK_NAME = ".lock"
_POLL_S = 0.2


class CycleLockTimeout(RuntimeError):
    """Another run of the cycle held the lock for longer than the caller would wait."""


def _try_lock(handle: IO[bytes]) -> bool:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class RunMarker:
    """What the lock file says while a run holds it."""

    cycle: str
    pid: int
    host: str
    started_at: float


def _read_marker(text: str) -> RunMarker | None:
    """The marker in a lock file's text; None for an empty file or a bare pid (before 1.6)."""
    try:
        data = json.loads(text)
        return RunMarker(
            cycle=str(data["cycle"]),
            pid=int(data["pid"]),
            host=str(data["host"]),
            started_at=float(data["started_at"]),
        )
    except (ValueError, TypeError, KeyError):
        return None


def _holder(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.isdigit():
        return f" (pid {text})"
    marker = _read_marker(text)
    return f" (pid {marker.pid} on {marker.host})" if marker else ""


def _left_behind(handle: IO[bytes]) -> RunMarker | None:
    handle.seek(0)
    try:
        return _read_marker(handle.read().decode("utf-8", errors="replace").strip())
    except OSError:
        return None


def take_unfinished(artifacts_root: Path, cycle: str) -> RunMarker | None:
    """The marker of a run of *cycle* that died holding its lock, cleared once read.

    None when there is none, or when a run of the cycle is going right now (its
    lock is held, so it isn't left behind). For the API at startup.
    """
    folder = plan_artifacts_dir(artifacts_root, cycle)
    path = folder / LOCK_NAME
    if not path.is_file():
        return None
    with open(path, "r+b") as handle:
        if not _try_lock(handle):
            return None
        try:
            marker = _left_behind(handle)
            if marker is not None:
                handle.seek(0)
                handle.truncate()
                handle.flush()
            return marker
        finally:
            _unlock(handle)


@contextmanager
def cycle_lock(
    artifacts_root: Path,
    cycle: str,
    *,
    on_wait: Callable[[str], None] | None = None,
    timeout_s: float | None = None,
    on_unfinished: Callable[[RunMarker], None] | None = None,
) -> Iterator[Path]:
    """Hold the cycle's lock for the ``with`` block; wait (calling *on_wait* once) while
    another run holds it. Raises :class:`CycleLockTimeout` after *timeout_s*.

    *on_unfinished* gets the marker of an earlier run that died holding the lock.
    """
    folder = plan_artifacts_dir(artifacts_root, cycle)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOCK_NAME
    handle = open(path, "a+b")  # noqa: SIM115 — closed in the finally below
    try:
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        told = False
        while not _try_lock(handle):
            if not told and on_wait is not None:
                on_wait(
                    f"another run of cycle {cycle!r} is using {folder}{_holder(path)}; "
                    "waiting for it to finish"
                )
                told = True
            if deadline is not None and time.monotonic() >= deadline:
                raise CycleLockTimeout(
                    f"another run of cycle {cycle!r} is still using {folder}{_holder(path)}"
                )
            time.sleep(_POLL_S)
        try:
            unfinished = _left_behind(handle)
            if unfinished is not None and on_unfinished is not None:
                on_unfinished(unfinished)
            marker = RunMarker(
                cycle=cycle, pid=os.getpid(), host=socket.gethostname(), started_at=time.time()
            )
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(asdict(marker)).encode())
            handle.flush()
            try:
                yield path
            finally:
                # The run ended (passed, failed, raised or was stopped): nothing left behind.
                handle.seek(0)
                handle.truncate()
                handle.flush()
        finally:
            _unlock(handle)
    finally:
        handle.close()
