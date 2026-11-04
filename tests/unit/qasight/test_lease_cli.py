"""``qasight lease``: hold a key while a command runs (qasight/cli/commands/lease.py)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.cli.commands.lease import parse_wait


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "qasight.yaml").write_text(
        "resources:\n"
        "  vms:\n"
        "    items: [vm-1, {id: vm-2, labels: [win], data: {address: 10.0.0.2}}]\n"
        "    lease_ttl: 5\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    for name in ("TESTO_SERVER_URL", "TESTO_LEASE_DB_URL", "TESTO_RESOURCE_SPEC"):
        monkeypatch.delenv(name, raising=False)
    from qasight.repository.db_config import reset_engine_cache

    reset_engine_cache()
    return tmp_path


def test_parse_wait() -> None:
    assert parse_wait("90s") == 90
    assert parse_wait("10m") == 600
    assert parse_wait("2h") == 7200
    assert parse_wait("0") == 0
    assert parse_wait("forever") is None


def test_run_passes_the_key_and_the_exit_code(project: Path) -> None:
    out = project / "seen.txt"
    code = (
        "import os, pathlib, sys; "
        f"pathlib.Path({str(out)!r}).write_text(os.environ['TESTO_RESOURCE_ID'] + ' ' + os.environ['TESTO_RESOURCE_ADDRESS']); "
        "sys.exit(3)"
    )
    result = CliRunner().invoke(
        app, ["lease", "run", "vms", "-l", "win", "--", sys.executable, "-c", code]
    )
    assert result.exit_code == 3, result.output
    assert out.read_text() == "vm-2 10.0.0.2"
    listed = CliRunner().invoke(app, ["lease", "list", "--json"])
    assert [i["lease"] for i in json.loads(listed.output)[0]["items"]] == [None, None]


def test_take_heartbeat_release_and_admin(project: Path) -> None:
    runner = CliRunner()
    taken = runner.invoke(app, ["lease", "take", "vms", "--json", "--ttl", "1h"])
    assert taken.exit_code == 0, taken.output
    lease = json.loads(taken.output)
    assert lease["key"] == "vm-1" and lease["env"]["TESTO_RESOURCE_ID"] == "vm-1"

    assert runner.invoke(app, ["lease", "heartbeat", lease["lease_id"]]).exit_code == 0
    listed = runner.invoke(app, ["lease", "list"])
    assert "1 free of 2" in listed.output and "held" in listed.output
    assert runner.invoke(app, ["lease", "release", lease["lease_id"]]).exit_code == 0
    assert runner.invoke(app, ["lease", "heartbeat", lease["lease_id"]]).exit_code == 1

    assert (
        runner.invoke(app, ["lease", "disable", "vms", "vm-1", "--reason", "broken"]).exit_code == 0
    )
    busy = runner.invoke(app, ["lease", "take", "vms", "--wait", "0", "-l", "win"])
    assert busy.exit_code == 0
    none_left = runner.invoke(app, ["lease", "take", "vms", "--wait", "0"])
    assert none_left.exit_code == 3
    assert "came free in time" in none_left.output
    assert runner.invoke(app, ["lease", "enable", "vms", "vm-1"]).exit_code == 0
    assert runner.invoke(app, ["lease", "free", "vms", "vm-2"]).exit_code == 0
    assert runner.invoke(app, ["lease", "free", "vms", "vm-2"]).exit_code == 1


def test_mistakes_exit_2(project: Path) -> None:
    runner = CliRunner()
    unknown = runner.invoke(app, ["lease", "run", "gpus", "--", "true"])
    assert unknown.exit_code == 2
    assert "no resource pool 'gpus'" in unknown.output
    assert runner.invoke(app, ["lease", "run", "vms"]).exit_code == 2


def test_parallel_processes_never_share_a_key(project: Path) -> None:
    """Six processes, two keys: each key has at most one holder at any moment."""
    log = project / "log.ndjson"
    worker = (
        "import json, os, time; "
        f"f = open({str(log)!r}, 'a'); "
        "f.write(json.dumps({'key': os.environ['TESTO_RESOURCE_ID'], 'start': time.time()}) + '\\n'); f.flush(); "
        "time.sleep(0.4); "
        "f.write(json.dumps({'key': os.environ['TESTO_RESOURCE_ID'], 'end': time.time()}) + '\\n'); f.close()"
    )
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "qasight.cli.app",
                "lease",
                "run",
                "vms",
                "--wait",
                "60s",
                "--",
                sys.executable,
                "-c",
                worker,
            ],
            cwd=project,
        )
        for _ in range(6)
    ]
    assert [p.wait(timeout=120) for p in procs] == [0] * 6
    spans: dict[str, list[tuple[float, float]]] = {}
    starts: dict[str, float] = {}
    for line in log.read_text().splitlines():
        event = json.loads(line)
        if "start" in event:
            assert event["key"] not in starts, f"{event['key']} had two holders"
            starts[event["key"]] = event["start"]
        else:
            spans.setdefault(event["key"], []).append((starts.pop(event["key"]), event["end"]))
    assert sum(len(v) for v in spans.values()) == 6
    assert set(spans) == {"vm-1", "vm-2"}
