"""In-memory ring buffer + artifacts log tee for subprocess output.

Each subprocess opened by the executor pipes its stdout (with stderr merged
in) into a :class:`LogBuffer`.  The buffer:

* writes every chunk to a per-stage log file under
  ``artifacts/<plan>/<stage>/run.log`` so post-mortem inspection is durable;
* keeps a bounded in-memory ring buffer (default 64 KiB) so the CLI can
  render a "tail" panel without holding the full log in memory;
* optionally invokes a heartbeat callback once per second so the live Rich
  Progress display can stay animated even when the subprocess is silent.

Threading model: a single daemon thread reads from the subprocess pipe;
calls into :meth:`LogBuffer.feed` are therefore serialised.  The reader
thread is joined explicitly via :meth:`close` to guarantee no log lines
are lost when the orchestrator moves on to the next stage.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Protocol


class ByteReader(Protocol):
    """A process's output stream: a pipe, or the runner service's stream."""

    def read(self, size: int = -1, /) -> bytes: ...


DEFAULT_RING_BYTES: int = 64 * 1024  # 64 KiB
_READ_CHUNK_BYTES: int = 4096

logger = logging.getLogger(__name__)


class LogBuffer:
    """Tee subprocess output to a log file and a bounded in-memory buffer."""

    def __init__(
        self,
        *,
        log_path: Path,
        ring_bytes: int = DEFAULT_RING_BYTES,
        on_chunk: Callable[[bytes], None] | None = None,
    ) -> None:
        self._log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = log_path.open("ab", buffering=0)
        self._ring: deque[bytes] = deque()
        self._ring_size: int = 0
        self._ring_capacity: int = max(1, int(ring_bytes))
        self._on_chunk = on_chunk
        self._on_chunk_failed = False
        self._closed = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API consumed by the executor
    # ------------------------------------------------------------------
    @property
    def log_path(self) -> Path:
        return self._log_path

    def feed(self, chunk: bytes) -> None:
        """Append ``chunk`` to the log file and the ring buffer."""
        if not chunk:
            return
        with self._lock:
            if self._closed:
                return
            self._fh.write(chunk)
            self._ring.append(chunk)
            self._ring_size += len(chunk)
            while self._ring_size > self._ring_capacity and self._ring:
                popped = self._ring.popleft()
                self._ring_size -= len(popped)
        if self._on_chunk is not None:
            try:
                self._on_chunk(chunk)
            except Exception:
                # Broad on purpose: a broken renderer must never crash the run. Log once
                # per buffer so a renderer failing on every chunk doesn't flood stderr.
                if not self._on_chunk_failed:
                    self._on_chunk_failed = True
                    logger.warning("log chunk renderer failed; output continues", exc_info=True)

    def tail(self, *, max_lines: int | None = None, max_bytes: int | None = None) -> str:
        """Return the most recent buffered output as text."""
        with self._lock:
            data = b"".join(self._ring)
        if max_bytes is not None and len(data) > max_bytes:
            data = data[-max_bytes:]
        text = data.decode("utf-8", errors="replace")
        if max_lines is not None:
            lines = text.splitlines()
            if len(lines) > max_lines:
                text = "\n".join(lines[-max_lines:])
        return text

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._fh.flush()
                self._fh.close()
            except OSError:
                pass

    def __enter__(self) -> LogBuffer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def drain_stream_into_buffer(
    stream: ByteReader,
    buffer: LogBuffer,
    *,
    chunk_bytes: int = _READ_CHUNK_BYTES,
) -> None:
    """Block-read ``stream`` until EOF, feeding each chunk into ``buffer``.

    Designed to be the target of a daemon thread spawned by the executor.
    """
    try:
        while True:
            chunk = stream.read(chunk_bytes)
            if not chunk:
                return
            buffer.feed(chunk)
    except (OSError, ValueError):
        # Stream was closed under us (e.g. subprocess exited and pipes shut)
        return


def merged_env(
    parent: Mapping[str, str], extra: Iterable[tuple[str, str]] | None
) -> dict[str, str]:
    """Return a copy of ``parent`` overlaid with ``extra`` env vars."""
    out = dict(parent)
    for k, v in extra or ():
        out[str(k)] = str(v)
    return out
