"""Client side of ``TESTO_RUNNER=container``: a stage process that runs in the runner service.

:class:`RemoteStageProcess` looks enough like :class:`subprocess.Popen` for the
executor: ``stdout`` to drain, ``wait(timeout)``, ``send_signal``/``kill``.
The service (:mod:`qasight.engine.backends.runner_service`) starts the
command in a fresh container and streams its output back.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import PurePath
from urllib.parse import urlsplit

from qasight.engine.backends import RunnerError
from qasight.engine.backends.runner_service import FRAME_EXIT, FRAME_OUTPUT, read_frame

RUNNER_URL_ENV = "TESTO_RUNNER_URL"
RUNNER_TOKEN_ENV = "TESTO_RUNNER_TOKEN"
# Several heartbeats long: the service sends one every 15 seconds of silence.
IDLE_TIMEOUT_S = 90.0
# The runner lost the stage (connection dropped, no exit code): like Docker's own 125.
LOST_RETURNCODE = 125
_PYTHON = re.compile(r"^python(\d+(\.\d+)?)?$")


def container_command(argv: Sequence[str]) -> list[str]:
    """The stage's argv as the image sees it: tools by name, found on the image's PATH.

    Adapters resolve tools to this interpreter's paths (``sys.executable``,
    ``.../bin/pytest``); those paths mean nothing inside the stage image.
    """
    if not argv:
        return []
    first = argv[0]
    if first == sys.executable or PurePath(first).is_absolute():
        first = PurePath(first).name
    if _PYTHON.match(first):
        first = "python"
    return [first, *argv[1:]]


class _FrameReader:
    """``stdout`` for the executor's reader thread: output frames as plain bytes."""

    def __init__(self, owner: RemoteStageProcess) -> None:
        self._owner = owner
        self._pending = b""

    def read(self, size: int = -1) -> bytes:
        while not self._pending:
            if self._owner.done.is_set():
                return b""
            self._pending = self._owner._next_output()
        if size < 0:
            size = len(self._pending)
        chunk, self._pending = self._pending[:size], self._pending[size:]
        return chunk

    def close(self) -> None:
        self._owner._close()


class RemoteStageProcess:
    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        url: str | None = None,
        token: str | None = None,
    ) -> None:
        url = url if url is not None else os.environ.get(RUNNER_URL_ENV, "").strip()
        token = token if token is not None else os.environ.get(RUNNER_TOKEN_ENV, "").strip()
        if not url or not token:
            raise RunnerError(
                f"TESTO_RUNNER=container needs {RUNNER_URL_ENV} and {RUNNER_TOKEN_ENV}"
            )
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise RunnerError(f"{RUNNER_URL_ENV}={url!r} is not an http(s) URL")
        self.args = list(argv)
        self.returncode: int | None = None
        self.done = threading.Event()
        self._parts = parts
        self._token = token
        self._lock = threading.Lock()
        body = json.dumps({"argv": container_command(argv), "cwd": cwd, "env": dict(env)})
        self._conn = self._connection(IDLE_TIMEOUT_S)
        try:
            self._conn.request(
                "POST", "/runs", body=body.encode(), headers=self._headers("application/json")
            )
            self._response = self._conn.getresponse()
        except OSError as exc:
            self._conn.close()
            raise RunnerError(f"cannot reach the runner service at {url}: {exc}") from exc
        if self._response.status != 200:
            detail = self._response.read(4096).decode(errors="replace")
            self._conn.close()
            raise RunnerError(
                f"the runner service refused the stage ({self._response.status}): {detail}"
            )
        self.name = self._response.getheader("X-Testo-Run", "")
        self.stdout = _FrameReader(self)

    def _connection(self, timeout: float | None) -> http.client.HTTPConnection:
        cls = (
            http.client.HTTPSConnection
            if self._parts.scheme == "https"
            else http.client.HTTPConnection
        )
        return cls(self._parts.hostname or "", self._parts.port, timeout=timeout)

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _next_output(self) -> bytes:
        """The next output frame's bytes; sets ``returncode`` at the end of the stream."""
        try:
            frame = read_frame(self._response)
        except (OSError, http.client.HTTPException):
            frame = None
        if frame is None:
            self._finish(LOST_RETURNCODE)
            return b"\n[qasight] lost the connection to the runner service before the stage ended\n"
        kind, payload = frame
        if kind == FRAME_EXIT:
            try:
                self._finish(int(payload))
            except ValueError:
                self._finish(LOST_RETURNCODE)
            return b""
        return payload if kind == FRAME_OUTPUT else b""

    def _finish(self, returncode: int) -> None:
        with self._lock:
            if self.returncode is None:
                self.returncode = returncode
        self.done.set()
        self._close()

    def _close(self) -> None:
        # The response holds the socket after a `Connection: close` reply; closing it
        # is what tells the service we are gone (it then stops the container).
        for closable in (getattr(self, "_response", None), self._conn):
            try:
                if closable is not None:
                    closable.close()
            except OSError:
                pass

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        assert self.returncode is not None  # noqa: S101 - set before done
        return self.returncode

    def send_signal(self, sig: int) -> None:
        self._stop("TERM" if sig == signal.SIGTERM else "KILL")

    def terminate(self) -> None:
        self._stop("TERM")

    def kill(self) -> None:
        self._stop("KILL")

    def _stop(self, signal_name: str) -> None:
        if self.done.is_set() or not self.name:
            return
        conn = self._connection(10.0)
        try:
            query = "?signal=TERM" if signal_name == "TERM" else ""
            conn.request("DELETE", f"/runs/{self.name}{query}", headers=self._headers())
            conn.getresponse().read()
        except (OSError, http.client.HTTPException):
            pass  # the stream ending (or the idle timeout) still finishes the stage
        finally:
            conn.close()
