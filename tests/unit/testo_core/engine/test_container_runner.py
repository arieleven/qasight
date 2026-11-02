"""``TESTO_RUNNER=container``: stages run through the runner service.

The service runs for real on a local port. Docker is replaced by a small fake
``docker`` script that starts the command on this machine with only the ``-e``
variables, records what it was asked, and honours ``docker kill``. So the HTTP
stream, the executor's timeout path and the env boundary are all exercised.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from testo_core.config.schema import Stage
from testo_core.engine.backends import RunnerError, launch, runner_kind
from testo_core.engine.backends.container import RemoteStageProcess, container_command
from testo_core.engine.backends.runner_service import (
    ConfigError,
    Mount,
    RequestError,
    RunnerSettings,
    RunSpec,
    docker_argv,
    make_server,
    parse_mounts,
    parse_run,
)
from testo_core.engine.executor import run_stage
from tests.fixtures.engine import HangAdapter, use_adapter, use_echo_adapter

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

TOKEN = "runner-test-token"

FAKE_DOCKER = """\
import json, os, pathlib, subprocess, sys, time
state = pathlib.Path(os.environ["DOCKER_FAKE_STATE"])
args = sys.argv[1:]
if args[0] == "kill":
    (state / ("kill-" + args[-1])).write_text(args[2])
    sys.exit(0)
if args[0] != "run":
    sys.exit(0)
names, i, name, workdir = [], 1, "", "/"
while not args[i].startswith("--entrypoint="):
    if args[i] == "-e":
        names.append(args[i + 1])
    if args[i] == "--name":
        name = args[i + 1]
    if args[i].startswith("--workdir="):
        workdir = args[i].split("=", 1)[1]
    i += 1 if args[i] in ("--rm", "--init") or "=" in args[i] else 2
entry, rest = args[i].split("=", 1)[1], args[i + 2:]
(state / ("run-" + name + ".json")).write_text(json.dumps({"argv": args, "env": names}))
env = {n: os.environ[n] for n in names}
env["PATH"] = os.environ["PATH"]
cmd = [sys.executable if entry == "python" else entry, *rest]
child = subprocess.Popen(cmd, cwd=workdir, env=env)
while child.poll() is None:
    if (state / ("kill-" + name)).exists():
        child.kill()
        child.wait()
        sys.exit(137)
    time.sleep(0.05)
sys.exit(child.returncode)
"""


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A runner service on a free port with the fake docker; yields its state dir."""
    state = tmp_path / "docker-state"
    state.mkdir()
    docker = tmp_path / "fake-docker"
    docker.write_text(f"#!{sys.executable}\n{FAKE_DOCKER}")
    docker.chmod(0o755)
    monkeypatch.setenv("DOCKER_FAKE_STATE", str(state))
    settings = RunnerSettings(
        token=TOKEN,
        mounts=(Mount("testo_repos", "/"),),
        docker=str(docker),
        host="127.0.0.1",
        port=0,
    )
    server = make_server(settings)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("TESTO_RUNNER", "container")
    monkeypatch.setenv("TESTO_RUNNER_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("TESTO_RUNNER_TOKEN", TOKEN)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _stage(*, args: tuple[str, ...] = (), timeout_s: float | None = 30.0, **kw: object) -> Stage:
    return Stage(
        name="boxed",
        framework="pytest",
        target_repo=Path("."),
        args=args,
        timeout_s=timeout_s,
        **kw,  # type: ignore[arg-type]
    )


def _recorded(state: Path) -> dict[str, list[str]]:
    (record,) = state.glob("run-*.json")
    return json.loads(record.read_text())


def test_stage_runs_in_the_runner_and_streams_output(
    runner: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    chunks: list[bytes] = []

    result = run_stage(
        _stage(args=("--text", "inside-the-box", "--exit-code", "3")),
        plan_name="demo",
        artifacts_root=tmp_path / "artifacts",
        on_chunk=chunks.append,
    )

    assert result.returncode == 3
    assert result.error is None
    assert "inside-the-box" in result.output_tail
    assert b"inside-the-box" in b"".join(chunks)
    assert "inside-the-box" in (tmp_path / "artifacts/demo/boxed/run.log").read_text()
    argv = _recorded(runner)["argv"]
    for flag in ("--rm", "--init", "--cap-drop", "no-new-privileges", "--pids-limit"):
        assert flag in argv
    # The interpreter's own path means nothing in the image: tools go by name.
    assert "--entrypoint=python" in argv


def test_only_the_stage_env_reaches_the_container(
    runner: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    monkeypatch.setenv("TESTO_API_TOKEN", "server-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    stage = _stage(
        args=(
            "--print-env",
            "TESTO_API_TOKEN",
            "--print-env",
            "DATABASE_URL",
            "--print-env",
            "MY_STAGE_VAR",
            "--print-env",
            "TESTO_LAST_TEST_TYPE",
        ),
        extra_env=(("MY_STAGE_VAR", "from-config"),),
    )

    result = run_stage(stage, plan_name="demo", artifacts_root=tmp_path / "artifacts")

    assert result.returncode == 0
    assert "TESTO_API_TOKEN=\n" in result.output_tail + "\n"
    assert "DATABASE_URL=\n" in result.output_tail + "\n"
    assert "MY_STAGE_VAR=from-config" in result.output_tail
    assert "TESTO_LAST_TEST_TYPE=pytest" in result.output_tail
    sent = set(_recorded(runner)["env"])
    assert "TESTO_API_TOKEN" not in sent and "DATABASE_URL" not in sent
    assert "server-secret" not in json.dumps(_recorded(runner))


def test_timeout_stops_the_container_and_returns_124(
    runner: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_adapter(monkeypatch, HangAdapter())

    result = run_stage(
        _stage(timeout_s=0.5), plan_name="demo", artifacts_root=tmp_path / "artifacts"
    )

    assert result.timed_out is True
    assert result.returncode == 124
    assert "hanging" in result.output_tail
    (kill,) = runner.glob("kill-*")
    assert kill.read_text() == "TERM"


def test_a_vanished_api_stops_the_container(runner: Path) -> None:
    from tests.fixtures.engine import HANG_SCRIPT

    proc = RemoteStageProcess(["python", str(HANG_SCRIPT)], cwd="/", env={})
    assert proc.stdout is not None and b"hanging" in proc.stdout.read(4096)
    proc._close()  # the API process died or dropped the connection

    # The fake `docker kill` creates the file before writing the signal, so wait for the text.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        kills = list(runner.glob("kill-*"))
        if kills and kills[0].read_text():
            break
        time.sleep(0.05)
    (kill,) = runner.glob("kill-*")
    assert kill.read_text() == "KILL"


def test_unreachable_runner_fails_the_stage_with_125(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_echo_adapter(monkeypatch)
    monkeypatch.setenv("TESTO_RUNNER", "container")
    monkeypatch.setenv("TESTO_RUNNER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TESTO_RUNNER_TOKEN", TOKEN)

    result = run_stage(_stage(), plan_name="demo", artifacts_root=tmp_path / "artifacts")

    assert result.returncode == 125
    assert result.error is not None and "cannot reach the runner service" in result.error


def test_runner_refuses_a_bad_token(runner: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RunnerError, match="refused the stage \\(401\\)"):
        RemoteStageProcess(["python", "-V"], cwd="/", env={}, token="wrong")


def test_runner_refuses_commands_outside_the_allow_list(runner: Path) -> None:
    with pytest.raises(RunnerError, match="not allowed here"):
        RemoteStageProcess(["/bin/sh", "-c", "id"], cwd="/", env={})


def test_host_is_the_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TESTO_RUNNER", raising=False)
    assert runner_kind() == "host"
    monkeypatch.setenv("TESTO_RUNNER", "spaceship")
    assert runner_kind() == "host"
    proc = launch([sys.executable, "-c", "print('hi')"], cwd=tmp_path, env=os.environ, stage_env={})
    assert proc.stdout is not None and proc.stdout.read(100).strip() == b"hi"
    assert proc.wait() == 0


def test_container_command_names_tools() -> None:
    assert container_command([sys.executable, "-m", "pytest"]) == ["python", "-m", "pytest"]
    assert container_command(["/opt/venv/bin/pytest", "-q"]) == ["pytest", "-q"]
    assert container_command(["/usr/bin/python3.12", "x.py"]) == ["python", "x.py"]
    assert container_command(["npx", "playwright", "test"]) == ["npx", "playwright", "test"]


# --- runner-side checks ----------------------------------------------------------


def _settings(**kw: object) -> RunnerSettings:
    base: dict[str, object] = {"token": TOKEN, "mounts": parse_mounts("repos:/repos:ro,arts:/arts")}
    return RunnerSettings(**{**base, **kw})  # type: ignore[arg-type]


def test_parse_mounts() -> None:
    assert parse_mounts("repos:/repos:ro, /srv/a:/arts") == (
        Mount("repos", "/repos", read_only=True),
        Mount("/srv/a", "/arts"),
    )
    assert (
        Mount("repos", "/repos", True).docker_arg() == "type=volume,src=repos,dst=/repos,readonly"
    )
    assert Mount("/srv/a", "/arts").docker_arg() == "type=bind,src=/srv/a,dst=/arts"
    for bad in ("repos", "repos:relative", "a:/b:rx", ":/b", "a:/b/../etc"):
        with pytest.raises(ConfigError):
            parse_mounts(bad)


def test_settings_need_a_token_and_mounts() -> None:
    with pytest.raises(ConfigError, match="TESTO_RUNNER_TOKEN"):
        RunnerSettings.from_env({"TESTO_RUNNER_MOUNTS": "a:/a"})
    with pytest.raises(ConfigError, match="TESTO_RUNNER_MOUNTS"):
        RunnerSettings.from_env({"TESTO_RUNNER_TOKEN": "t"})
    settings = RunnerSettings.from_env(
        {
            "TESTO_RUNNER_TOKEN": "t",
            "TESTO_RUNNER_MOUNTS": "a:/a",
            "TESTO_RUNNER_ALLOWED_COMMANDS": "pytest, npx",
            "TESTO_RUNNER_NETWORK": "none",
            "TESTO_RUNNER_MEMORY": "2g",
        }
    )
    assert settings.commands == {"pytest", "npx"}
    argv = docker_argv(settings, RunSpec(("pytest",), "/a"), "testo-x")
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--memory") + 1] == "2g"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ([], "JSON object"),
        ({"argv": [], "cwd": "/repos"}, "non-empty list"),
        ({"argv": ["bash"], "cwd": "/repos"}, "not allowed"),
        ({"argv": ["./run.sh"], "cwd": "/repos"}, "not allowed"),
        ({"argv": ["pytest"], "cwd": "repos"}, "absolute"),
        ({"argv": ["pytest"], "cwd": "/etc"}, "not inside a mounted"),
        ({"argv": ["pytest"], "cwd": "/repos/../etc"}, "not inside a mounted"),
        ({"argv": ["pytest"], "cwd": "/repos", "env": {"BAD-NAME": "x"}}, "variable name"),
        ({"argv": ["pytest"], "cwd": "/repos", "env": {"A": 1}}, "strings"),
    ],
)
def test_parse_run_refuses(body: object, message: str) -> None:
    with pytest.raises(RequestError, match=message):
        parse_run(body, _settings())


def test_parse_run_drops_variables_that_steer_docker() -> None:
    spec = parse_run(
        {
            "argv": ["pytest"],
            "cwd": "/repos/app",
            "env": {"DOCKER_HOST": "tcp://evil", "PATH": "/x", "CI": "1"},
        },
        _settings(),
    )
    assert spec.env == {"CI": "1"}
    assert parse_run({"argv": ["./run.sh"], "cwd": "/arts"}, _settings(commands={"*"})).argv == (
        "./run.sh",
    )


def test_docker_argv_keeps_values_out_of_the_command_line() -> None:
    spec = RunSpec(("pytest", "-q"), "/repos/app", {"API_KEY": "s3cret"})
    argv = docker_argv(_settings(), spec, "testo-abc")
    assert "s3cret" not in " ".join(argv)
    assert argv[argv.index("-e") + 1] == "API_KEY"
    assert argv[-4:] == [
        "--workdir=/repos/app",
        "--entrypoint=pytest",
        "ghcr.io/taltal-beep/testo-runner:1",
        "-q",
    ]
    assert "type=volume,src=repos,dst=/repos,readonly" in argv
