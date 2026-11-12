"""The runner service: starts each stage in a fresh, throwaway container.

Used when the API runs with ``TESTO_RUNNER=container``. The service is the only
process that can talk to Docker (or Podman); the API never gets the socket. It
takes a stage's command over HTTP, runs it with ``docker run --rm`` and streams
the output back. What the container gets is fixed here, not by the request:

- the image (``TESTO_RUNNER_IMAGE``),
- the mounts (``TESTO_RUNNER_MOUNTS``; the stage's directory must be inside one),
- the commands it may start (``TESTO_RUNNER_ALLOWED_COMMANDS``),
- no Linux capabilities, no privilege escalation, a process limit and optional
  memory, CPU and network limits.

The request carries only the command, its directory and the stage's own
environment variables, never the server's (so no API token or database URL).

Standard library only, so it runs from this one file:
``python runner_service.py`` or the ``qasight-runner-service`` command.

Wire format of ``POST /runs``: the response body is a stream of frames, each
one byte of kind, four bytes of big-endian length, then the payload. Kind ``o``
is output (an empty one is a heartbeat), kind ``x`` is the exit code in ASCII
and always comes last.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import select
import socket
import struct
import subprocess
import sys
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from typing import Any, Protocol

logger = logging.getLogger("testo.runner_service")

FRAME_OUTPUT = b"o"
FRAME_EXIT = b"x"
_HEADER = struct.Struct(">cI")
HEARTBEAT_S = 15.0
_READ_CHUNK = 64 * 1024
_MAX_BODY = 1024 * 1024
RUN_LABEL = "testo.runner=1"

DEFAULT_IMAGE = "ghcr.io/arieleven/qasight-runner:1"
DEFAULT_COMMANDS = ("python", "python3", "pytest", "behave", "behavex", "node", "npm", "npx")
DEFAULT_USER = "10001:10001"
DEFAULT_PORT = 8790

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# The docker CLI reads these from its own environment; a stage must not steer it.
_RESERVED_ENV = ("PATH", "HOME")
_RESERVED_ENV_PREFIXES = ("DOCKER_",)


class _Writer(Protocol):
    def write(self, data: bytes, /) -> Any: ...

    def flush(self) -> Any: ...


class _Reader(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


# --- frames (shared with the client in qasight.engine.backends.container) ---


def write_frame(stream: _Writer, kind: bytes, payload: bytes = b"") -> None:
    stream.write(_HEADER.pack(kind, len(payload)) + payload)
    stream.flush()


def read_frame(stream: _Reader) -> tuple[bytes, bytes] | None:
    """The next ``(kind, payload)``, or None when the stream ended."""
    header = _read_exact(stream, _HEADER.size)
    if header is None:
        return None
    kind, length = _HEADER.unpack(header)
    payload = _read_exact(stream, length) if length else b""
    return None if payload is None else (kind, payload)


def _read_exact(stream: _Reader, size: int) -> bytes | None:
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            return None
        data += chunk
    return data


# --- configuration -------------------------------------------------------------


class ConfigError(ValueError):
    """The service's own settings are wrong; it refuses to start."""


@dataclass(frozen=True)
class Mount:
    source: str
    target: str
    read_only: bool = False

    def docker_arg(self) -> str:
        kind = "bind" if self.source.startswith("/") else "volume"
        arg = f"type={kind},src={self.source},dst={self.target}"
        return arg + (",readonly" if self.read_only else "")

    def contains(self, path: PurePosixPath) -> bool:
        target = PurePosixPath(self.target)
        return path == target or target in path.parents


def parse_mounts(raw: str) -> tuple[Mount, ...]:
    """``source:target[:ro]`` entries, comma separated. Targets must be absolute."""
    mounts: list[Mount] = []
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] not in ("ro", "rw")):
            raise ConfigError(f"mount {entry!r} is not source:target[:ro]")
        source, target = parts[0], parts[1]
        if not source or not target.startswith("/") or ".." in PurePosixPath(target).parts:
            raise ConfigError(f"mount {entry!r} needs a source and an absolute target")
        mounts.append(Mount(source, target, read_only=len(parts) == 3 and parts[2] == "ro"))
    return tuple(mounts)


@dataclass(frozen=True)
class RunnerSettings:
    token: str
    mounts: tuple[Mount, ...]
    image: str = DEFAULT_IMAGE
    commands: frozenset[str] = frozenset(DEFAULT_COMMANDS)
    user: str = DEFAULT_USER
    network: str | None = None
    memory: str | None = None
    cpus: str | None = None
    pids_limit: int = 512
    docker: str = "docker"
    max_runs: int = 4
    host: str = "0.0.0.0"  # noqa: S104 - it serves its own container network  # nosec B104
    port: int = DEFAULT_PORT

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunnerSettings:
        env = os.environ if env is None else env

        def opt(name: str) -> str | None:
            value = (env.get(name) or "").strip()
            return value or None

        token = opt("TESTO_RUNNER_TOKEN")
        if token is None:
            raise ConfigError("TESTO_RUNNER_TOKEN is required: the API sends it with every run")
        mounts = parse_mounts(env.get("TESTO_RUNNER_MOUNTS", ""))
        if not mounts:
            raise ConfigError(
                "TESTO_RUNNER_MOUNTS is required, e.g. testo_repos:/repos,testo_artifacts:/artifacts"
            )
        commands = opt("TESTO_RUNNER_ALLOWED_COMMANDS")
        try:
            return cls(
                token=token,
                mounts=mounts,
                image=opt("TESTO_RUNNER_IMAGE") or DEFAULT_IMAGE,
                commands=frozenset(c.strip() for c in commands.split(",") if c.strip())
                if commands
                else frozenset(DEFAULT_COMMANDS),
                user=opt("TESTO_RUNNER_USER") or DEFAULT_USER,
                network=opt("TESTO_RUNNER_NETWORK"),
                memory=opt("TESTO_RUNNER_MEMORY"),
                cpus=opt("TESTO_RUNNER_CPUS"),
                pids_limit=int(opt("TESTO_RUNNER_PIDS_LIMIT") or 512),
                docker=opt("TESTO_RUNNER_DOCKER") or "docker",
                max_runs=max(1, int(opt("TESTO_RUNNER_MAX_RUNS") or 4)),
                host=opt("TESTO_RUNNER_HOST") or "0.0.0.0",  # noqa: S104  # nosec B104
                port=int(opt("TESTO_RUNNER_PORT") or DEFAULT_PORT),
            )
        except ValueError as exc:
            raise ConfigError(f"bad runner setting: {exc}") from exc


# --- one run -------------------------------------------------------------------


class RequestError(ValueError):
    """The request asks for something this runner doesn't allow."""


@dataclass(frozen=True)
class RunSpec:
    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str] = field(default_factory=dict)


def parse_run(body: Any, settings: RunnerSettings) -> RunSpec:
    if not isinstance(body, dict):
        raise RequestError("body must be a JSON object")
    argv, cwd, env = body.get("argv"), body.get("cwd"), body.get("env") or {}
    if (
        not isinstance(argv, list)
        or not argv
        or len(argv) > 4096
        or not all(isinstance(a, str) and "\0" not in a for a in argv)
    ):
        raise RequestError("argv must be a non-empty list of strings")
    command = argv[0]
    if "*" not in settings.commands and ("/" in command or command not in settings.commands):
        allowed = ", ".join(sorted(settings.commands))
        raise RequestError(f"command {command!r} is not allowed here (allowed: {allowed})")
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        raise RequestError("cwd must be an absolute path")
    path = PurePosixPath(cwd)
    if ".." in path.parts or not any(m.contains(path) for m in settings.mounts):
        raise RequestError(f"cwd {cwd!r} is not inside a mounted directory")
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and "\0" not in v for k, v in env.items()
    ):
        raise RequestError("env must map names to strings")
    clean: dict[str, str] = {}
    for name, value in env.items():
        if not _ENV_NAME.match(name):
            raise RequestError(f"bad environment variable name {name!r}")
        if name in _RESERVED_ENV or name.startswith(_RESERVED_ENV_PREFIXES):
            continue
        clean[name] = value
    return RunSpec(tuple(argv), cwd, clean)


def docker_argv(settings: RunnerSettings, spec: RunSpec, name: str) -> list[str]:
    """The ``docker run`` command for one stage. Values of ``-e NAME`` come from its env."""
    argv = [
        settings.docker,
        "run",
        "--rm",
        "--name",
        name,
        "--label",
        RUN_LABEL,
        "--init",
        "--user",
        settings.user,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(settings.pids_limit),
    ]
    if settings.memory:
        argv += ["--memory", settings.memory]
    if settings.cpus:
        argv += ["--cpus", settings.cpus]
    if settings.network:
        argv += ["--network", settings.network]
    for mount in settings.mounts:
        argv += ["--mount", mount.docker_arg()]
    for env_name in sorted(spec.env):
        argv += ["-e", env_name]
    # `--flag=value`, so a value that starts with `-` can't be read as another flag.
    argv += [f"--workdir={spec.cwd}", f"--entrypoint={spec.argv[0]}", settings.image]
    return argv + list(spec.argv[1:])


def docker_env(spec: RunSpec) -> dict[str, str]:
    """The docker CLI's environment: the stage's variables, then the service's own."""
    own = {
        k: v
        for k, v in os.environ.items()
        if k in _RESERVED_ENV or k.startswith(_RESERVED_ENV_PREFIXES)
    }
    return {**spec.env, **own}


def _closed(client: socket.socket) -> bool:
    """A readable client socket with nothing to read has been closed by the client."""
    try:
        return client.recv(1, socket.MSG_PEEK) == b""
    except BlockingIOError:
        return False
    except OSError:
        return True


class Runner:
    def __init__(self, settings: RunnerSettings) -> None:
        self.settings = settings
        self._slots = threading.BoundedSemaphore(settings.max_runs)
        self._lock = threading.Lock()
        self._active: set[str] = set()

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._active)

    def is_active(self, name: str) -> bool:
        with self._lock:
            return name in self._active

    def run(
        self, spec: RunSpec, name: str, out: _Writer, client: socket.socket | None = None
    ) -> int:
        """Run one stage, streaming frames to ``out``. Returns the exit code.

        ``client`` is the caller's connection: when it closes, the container is stopped.
        """
        with self._slots:
            with self._lock:
                self._active.add(name)
            try:
                return self._run(spec, name, out, client)
            finally:
                with self._lock:
                    self._active.discard(name)

    def _run(self, spec: RunSpec, name: str, out: _Writer, client: socket.socket | None) -> int:
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv built above from checked input
                docker_argv(self.settings, spec, name),
                env=docker_env(spec),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except OSError as exc:
            message = f"[qasight-runner] cannot start {self.settings.docker!r}: {exc}\n"
            write_frame(out, FRAME_OUTPUT, message.encode())
            write_frame(out, FRAME_EXIT, b"125")
            return 125
        assert proc.stdout is not None  # noqa: S101 - we asked for a pipe
        fd = proc.stdout.fileno()
        try:
            client_fd = -1 if client is None else client.fileno()
            watched = [fd] if client is None else [fd, client_fd]
            while True:
                ready, _, _ = select.select(watched, [], [], HEARTBEAT_S)
                if client is not None and client_fd in ready and _closed(client):
                    raise ConnectionResetError("client closed the connection")
                chunk = os.read(fd, _READ_CHUNK) if fd in ready else b""
                if fd in ready and not chunk:
                    break
                # An empty frame is a heartbeat; it also notices a client that vanished.
                write_frame(out, FRAME_OUTPUT, chunk)
            returncode = proc.wait()
            write_frame(out, FRAME_EXIT, str(returncode).encode())
            return returncode
        except (BrokenPipeError, ConnectionError, OSError):
            logger.warning("client went away; stopping %s", name)
            self.kill(name)
            return proc.wait()
        finally:
            proc.stdout.close()

    def kill(self, name: str, signal_name: str = "KILL") -> None:
        subprocess.run(  # noqa: S603
            [self.settings.docker, "kill", "--signal", signal_name, name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def remove_leftovers(self) -> None:
        """Remove stage containers a crashed earlier service left behind."""
        try:
            found = subprocess.run(  # noqa: S603
                [self.settings.docker, "ps", "-aq", "--filter", f"label={RUN_LABEL}"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.split()
        except OSError:
            return
        if found:
            subprocess.run(  # noqa: S603
                [self.settings.docker, "rm", "-f", *found],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


# --- HTTP ----------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = "qasight-runner"
    runner: Runner  # set on the subclass made by make_server

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.info("%s %s", self.address_string(), format % args)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {self.runner.settings.token}"
        return hmac.compare_digest(header.encode(), expected.encode())

    def _reply(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._reply(HTTPStatus.OK, {"status": "ok", "active": self.runner.active})
        else:
            self._reply(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/runs":
            self._reply(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        if not self._authorized():
            self._reply(HTTPStatus.UNAUTHORIZED, {"detail": "bad or missing token"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= _MAX_BODY:
                raise RequestError("body must be 1 byte to 1 MB of JSON")
            spec = parse_run(json.loads(self.rfile.read(length)), self.runner.settings)
        except (RequestError, ValueError) as exc:
            self._reply(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
            return
        name = f"testo-{uuid.uuid4().hex[:16]}"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("X-Testo-Run", name)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        returncode = self.runner.run(spec, name, self.wfile, self.connection)
        logger.info("run %s (%s) exited %s", name, spec.argv[0], returncode)

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._authorized():
            self._reply(HTTPStatus.UNAUTHORIZED, {"detail": "bad or missing token"})
            return
        name, _, query = self.path.removeprefix("/runs/").partition("?")
        if not self.path.startswith("/runs/") or not self.runner.is_active(name):
            self._reply(HTTPStatus.NOT_FOUND, {"detail": "no such run"})
            return
        signal_name = "TERM" if query == "signal=TERM" else "KILL"
        self.runner.kill(name, signal_name)
        self._reply(HTTPStatus.ACCEPTED, {"run": name, "signal": signal_name})


def make_server(settings: RunnerSettings) -> ThreadingHTTPServer:
    handler = type("RunnerHandler", (_Handler,), {"runner": Runner(settings)})
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    server.daemon_threads = True
    return server


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = RunnerSettings.from_env()
    except ConfigError as exc:
        print(f"qasight-runner-service: {exc}", file=sys.stderr)
        return 2
    server = make_server(settings)
    server.RequestHandlerClass.runner.remove_leftovers()  # type: ignore[attr-defined]
    logger.info(
        "qasight runner service on %s:%s, image %s, %d mount(s)",
        settings.host,
        settings.port,
        settings.image,
        len(settings.mounts),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
