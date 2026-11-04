"""Where a stage's process runs: on this machine (default) or in a throwaway container.

``TESTO_RUNNER`` picks the backend:

- ``host`` (default): :class:`subprocess.Popen`, as always.
- ``container``: the runner service (``TESTO_RUNNER_URL``) starts the stage in a
  fresh container and streams its output back. Only the stage's own variables
  travel with it (``stage_env``: its ``env:`` block and what QASight and the
  adapter set), never this process's environment.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from qasight.engine.log_buffer import ByteReader

logger = logging.getLogger(__name__)

RUNNER_ENV = "TESTO_RUNNER"
RunnerKind = Literal["host", "container"]


class RunnerError(RuntimeError):
    """The runner service is not configured, unreachable, or refused the stage."""


class StageProcess(Protocol):
    """The part of :class:`subprocess.Popen` the executor uses."""

    @property
    def stdout(self) -> ByteReader | None: ...

    @property
    def returncode(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def send_signal(self, sig: int) -> None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class HostStageProcess(subprocess.Popen[bytes]):
    """A host stage, started as the leader of its own process group.

    Signals go to the whole group, so a timeout or Ctrl-C also stops what the stage
    started (``sh -c 'server & pytest'``, pytest-xdist workers, browsers), not just
    the direct child. On Windows the group is killed with ``taskkill /T``.
    """

    def send_signal(self, sig: int) -> None:
        if sys.platform == "win32":
            if sig in (signal.SIGTERM, getattr(signal, "SIGKILL", -1)):
                self._taskkill(force=sig != signal.SIGTERM)
                return
            super().send_signal(sig)
            return
        try:
            # After the leader is reaped its pid can't be a group id unless members
            # remain, so this only reaches what the stage left behind.
            os.killpg(self.pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            super().send_signal(sig)

    def terminate(self) -> None:
        self.send_signal(signal.SIGTERM)

    def kill(self) -> None:
        if sys.platform == "win32":
            self._taskkill(force=True)
            return
        self.send_signal(signal.SIGKILL)

    def _taskkill(self, *, force: bool) -> None:
        argv = ["taskkill", "/T", "/PID", str(self.pid)] + (["/F"] if force else [])
        try:
            subprocess.run(argv, capture_output=True, timeout=10, check=False)  # noqa: S603
        except (OSError, subprocess.SubprocessError):
            super().kill() if force else super().terminate()


def runner_kind() -> RunnerKind:
    raw = (os.environ.get(RUNNER_ENV) or "host").strip().lower()
    if raw in ("host", "container"):
        return raw  # type: ignore[return-value]
    logger.warning("%s=%r is not 'host' or 'container'; using host", RUNNER_ENV, raw)
    return "host"


def launch(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    stage_env: Mapping[str, str],
) -> StageProcess:
    """Start a stage with stdout and stderr merged into one ``stdout`` stream."""
    if runner_kind() == "container":
        from qasight.engine.backends.container import RemoteStageProcess

        return RemoteStageProcess(argv, cwd=str(cwd), env=stage_env)
    group: dict[str, Any] = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    return HostStageProcess(  # noqa: S603 - argv is built by trusted adapters
        list(argv),
        cwd=str(cwd),
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        close_fds=True,
        **group,
    )
