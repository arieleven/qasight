"""A stage's ``serve:``: start the app its tests need, wait until it answers, stop it after.

End-to-end runners (Cypress, Selenium suites, Behave against a web app) need
the app running first. Without this, CI has to start it in a separate step
and ``qasight run`` can't run the stage on its own. The command runs through
the shell in the stage's ``target_repo`` with the stage's environment, its
output goes to ``serve.log`` next to the stage's ``run.log``, and the whole
process group is stopped when the stage ends, pass or fail.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from pathlib import Path

from qasight.config.schema import StageServe

_POLL_S = 0.5
_STOP_GRACE_S = 10.0


class ServeError(RuntimeError):
    """The app didn't start: its command exited, or the URL never answered in time."""


def _answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5):  # noqa: S310  # nosec B310 - config http(s) URL
            return True
    except urllib.error.HTTPError as exc:
        return exc.code < 500  # a 404 on "/" still means the server is up
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=_STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    except (ProcessLookupError, OSError):
        pass


def _tail(path: Path, lines: int = 15) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


@contextlib.contextmanager
def serving(
    serve: StageServe,
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_path: Path,
) -> Iterator[None]:
    """Run *serve*'s command until the block ends; raise :class:`ServeError` if it never answers."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        proc = subprocess.Popen(  # noqa: S602  # nosec B602 - the config author's command
            serve.command,
            shell=True,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
        )
    try:
        deadline = time.monotonic() + serve.timeout_s
        while not _answers(serve.url):
            code = proc.poll()
            if code is not None:
                raise ServeError(
                    f"`{serve.command}` exited with code {code} before {serve.url} answered"
                    + (f"; its last output:\n{tail}" if (tail := _tail(log_path)) else "")
                )
            if time.monotonic() >= deadline:
                raise ServeError(
                    f"{serve.url} didn't answer within {serve.timeout_s:g}s of starting "
                    f"`{serve.command}` (see {log_path.name})"
                )
            time.sleep(_POLL_S)
        yield
    finally:
        _stop(proc)
