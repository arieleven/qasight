"""A stage's ``resources:``: per stage, per worker, per test, and ``workers: auto``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import create_engine

from qasight.config.errors import ConfigValidationError
from qasight.config.loader import load_config
from qasight.engine.executor import run_stage
from qasight.resources.store import Holder, LeaseStore

_POOL = "resources:\n  vms:\n    items: [vm-1, vm-2, {id: vm-3, labels: [win]}]\n"


def _config(tmp_path: Path, stage: str) -> Path:
    path = tmp_path / "qasight.yaml"
    path.write_text(_POOL + "cycles:\n  c:\n    stages:\n" + stage, encoding="utf-8")
    return path


def test_stage_resources_parse(tmp_path: Path) -> None:
    config = load_config(
        _config(
            tmp_path,
            "      - {name: s, framework: pytest, workers: auto,"
            " resources: {pool: vms, per: test, labels: [win], wait: 10m}}\n"
            "      - {name: t, framework: command, args: [true], resources: vms}\n",
        )
    )
    first, second = config.cycles["c"].stages
    assert first.workers_auto and first.resources is not None
    assert (first.resources.pool.name, first.resources.per, first.resources.labels) == (
        "vms",
        "test",
        ("win",),
    )
    assert first.resources.wait_s == 600
    assert second.resources is not None and second.resources.per == "worker"
    assert not second.workers_auto


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        ("{name: s, framework: pytest, resources: gpus}", "resources.pool 'gpus' is not a pool"),
        (
            "{name: s, framework: pytest, resources: {pool: vms, per: run}}",
            "resources.per must be one of",
        ),
        (
            "{name: s, framework: pytest, resources: {pool: vms, labels: [gpu]}}",
            "has the labels ['gpu']",
        ),
        ("{name: s, framework: pytest, workers: auto}", "'workers: auto' needs 'resources'"),
        (
            "{name: s, framework: pytest, workers: auto, resources: {pool: vms, per: stage}}",
            "'workers: auto' needs",
        ),
        (
            "{name: s, framework: command, args: [x], resources: {pool: vms, per: test}}",
            "per: test works with pytest only",
        ),
        ("{name: s, framework: pytest, resources: {pool: vms, wait: soon}}", "resources.wait"),
    ],
)
def test_stage_resources_mistakes(tmp_path: Path, stage: str, message: str) -> None:
    with pytest.raises(ConfigValidationError) as info:
        load_config(_config(tmp_path, f"      - {stage}\n"))
    assert message in str(info.value)


@pytest.fixture
def lease_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'leases.db'}"
    monkeypatch.setenv("TESTO_LEASE_DB_URL", url)
    monkeypatch.delenv("TESTO_SERVER_URL", raising=False)
    monkeypatch.delenv("TESTO_RESOURCE_SPEC", raising=False)
    return url


def test_per_stage_holds_one_key_while_the_stage_runs(tmp_path: Path, lease_db: str) -> None:
    out = tmp_path / "seen.txt"
    stage_yaml = (
        "      - name: s\n        framework: command\n"
        f"        args: [{json.dumps(sys.executable)}, -c, "
        f"\"import os, pathlib; pathlib.Path({str(out)!r}).write_text(os.environ['TESTO_RESOURCE_ID'])\"]\n"
        "        resources: {pool: vms, per: stage, labels: [win]}\n"
    )
    stage = load_config(_config(tmp_path, stage_yaml)).cycles["c"].stages[0]
    result = run_stage(stage, plan_name="c", artifacts_root=tmp_path / "artifacts")
    assert result.returncode == 0, result.output_tail
    assert out.read_text() == "vm-3"
    log = result.log_path.read_text()  # type: ignore[union-attr]
    assert "[qasight] resources: holding vms/vm-3 for this stage" in log
    # Given back afterwards.
    store = LeaseStore(create_engine(lease_db))
    assert all(i.lease is None for p in store.status("vms") for i in p.items)


def test_per_stage_fails_like_a_timeout_when_no_key_comes(tmp_path: Path, lease_db: str) -> None:
    stage_yaml = (
        "      - {name: s, framework: command, args: [true],"
        " resources: {pool: vms, per: stage, labels: [win], wait: 0}}\n"
    )
    config = load_config(_config(tmp_path, stage_yaml))
    store = LeaseStore(create_engine(lease_db))
    assert store.take(config.resources["vms"], labels=["win"], holder=Holder(user="other")).lease
    result = run_stage(config.cycles["c"].stages[0], plan_name="c", artifacts_root=tmp_path / "a")
    assert result.returncode == 124
    assert result.error is not None and result.error.startswith("no key from resource pool")


def test_container_mode_never_runs_per_stage_hooks_on_the_server(
    tmp_path: Path, lease_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The hooks would run in this process, i.e. the API server, outside the container.
    marker = tmp_path / "ran-on-server"
    path = tmp_path / "qasight.yaml"
    path.write_text(
        "resources:\n  vms:\n    items: [vm-1]\n"
        f"    on_take: {json.dumps(f'touch {marker}')}\n"
        "cycles:\n  c:\n    stages:\n"
        "      - {name: s, framework: command, args: [true],"
        " resources: {pool: vms, per: stage, wait: 0}}\n",
        encoding="utf-8",
    )
    stage = load_config(path).cycles["c"].stages[0]
    monkeypatch.setenv("TESTO_RUNNER", "container")
    result = run_stage(stage, plan_name="c", artifacts_root=tmp_path / "a")
    assert result.returncode == 124
    assert not marker.exists()
    assert "on_take" in (result.error or "") and "per: test" in (result.error or "")
    # Nothing was leased either.
    store = LeaseStore(create_engine(lease_db))
    assert all(i.lease is None for p in store.status("vms") for i in p.items)


def test_per_worker_hands_the_pool_down_and_auto_counts_free_keys(
    tmp_path: Path, lease_db: str
) -> None:
    out = tmp_path / "env.json"
    stage_yaml = (
        "      - name: s\n        framework: command\n        workers: auto\n"
        f"        args: [{json.dumps(sys.executable)}, -c, "
        f'"import os, json, pathlib; pathlib.Path({str(out)!r}).write_text(json.dumps(dict(os.environ)))"]\n'
        "        resources: vms\n"
    )
    config = load_config(_config(tmp_path, stage_yaml))
    store = LeaseStore(create_engine(lease_db))
    assert store.take(config.resources["vms"], holder=Holder(user="other")).lease
    result = run_stage(config.cycles["c"].stages[0], plan_name="c", artifacts_root=tmp_path / "a")
    assert result.returncode == 0, result.output_tail
    env = json.loads(out.read_text())
    spec = json.loads(env["TESTO_RESOURCE_SPEC"])
    assert (spec["pool"], spec["per"], spec["stage"]) == ("vms", "worker", "c/s")
    assert env["TESTO_RESOURCE_POOL"] == "vms"
    assert env["TESTO_LEASE_DB_URL"] == lease_db
    assert "TESTO_RESOURCE_ID" not in env  # each worker takes its own
    assert (env["TESTO_CYCLE"], env["TESTO_STAGE"]) == ("c", "s")
    log = result.log_path.read_text()  # type: ignore[union-attr]
    assert "workers: auto -> 2 (2 free key(s) in pool 'vms')" in log


def _suite(tmp_path: Path) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_keys.py").write_text(
        "import os, pathlib, time\n"
        "def _rec(name):\n"
        "    start = time.time()\n"
        "    time.sleep(0.2)\n"
        "    with open(pathlib.Path(__file__).parent / 'seen.log', 'a') as f:\n"
        "        f.write(f\"{name} {os.environ.get('PYTEST_XDIST_WORKER', 'main')} "
        "{os.environ['TESTO_RESOURCE_ID']} {os.environ['TESTO_LEASE_ID']} {start} {time.time()}\\n\")\n"
        "def test_a(testo_resource):\n"
        "    assert testo_resource.id == os.environ['TESTO_RESOURCE_ID']\n"
        "    _rec('a')\n"
        "def test_b(): _rec('b')\n"
        "def test_c(): _rec('c')\n"
        "def test_d(): _rec('d')\n",
        encoding="utf-8",
    )
    (tmp_path / "qasight.yaml").write_text(
        _POOL + "    on_take: 'echo $TESTO_RESOURCE_ID >> takes.log'\n", encoding="utf-8"
    )
    return suite


def _pytest(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--no-cov",
            *args,
            "suite",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _seen(suite: Path) -> list[list[str]]:
    return [line.split() for line in (suite / "seen.log").read_text().splitlines()]


def test_pytest_holds_one_key_per_session(tmp_path: Path, lease_db: str) -> None:
    suite = _suite(tmp_path)
    done = _pytest(tmp_path, "-p", "no:xdist", "--qasight-resource", "vms")
    assert done.returncode == 0, done.stdout + done.stderr
    seen = _seen(suite)
    assert {row[2] for row in seen} == {"vm-1"} and len({row[3] for row in seen}) == 1
    assert (tmp_path / "takes.log").read_text().split() == ["vm-1"]


def test_pytest_takes_a_key_per_test(tmp_path: Path, lease_db: str) -> None:
    suite = _suite(tmp_path)
    done = _pytest(
        tmp_path,
        "-p",
        "no:xdist",
        "--qasight-resource",
        "vms",
        "--qasight-resource-per",
        "test",
        "--qasight-resource-label",
        "win",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    seen = _seen(suite)
    # A new lease (and an on_take run) for every test.
    assert {row[2] for row in seen} == {"vm-3"} and len({row[3] for row in seen}) == 4
    assert (tmp_path / "takes.log").read_text().split() == ["vm-3"] * 4


def test_testo_resource_without_a_pool_fails_the_test(tmp_path: Path, lease_db: str) -> None:
    _suite(tmp_path)
    done = _pytest(tmp_path, "-p", "no:xdist", "-k", "test_a")
    assert done.returncode == 1
    assert "testo_resource needs a pool" in done.stdout


def test_pytest_xdist_workers_never_share_a_key(tmp_path: Path, lease_db: str) -> None:
    pytest.importorskip("xdist")
    suite = _suite(tmp_path)
    done = _pytest(tmp_path, "-n", "2", "--qasight-resource", "vms")
    assert done.returncode == 0, done.stdout + done.stderr
    # One lease per worker (the controller takes none), spanning that worker's tests.
    spans: dict[str, tuple[str, str, float, float]] = {}
    for _name, worker, key, lease, start, end in _seen(suite):
        assert worker.startswith("gw")
        _, _, first, last = spans.get(lease, (worker, key, float(start), float(end)))
        spans[lease] = (worker, key, min(first, float(start)), max(last, float(end)))
    assert len({worker for worker, *_ in spans.values()}) == len(spans)
    # Workers may hold the same key one after the other, never at the same time.
    ordered = sorted(spans.values(), key=lambda span: span[2])
    for i, (_, key, _, end) in enumerate(ordered):
        assert all(other[1] != key or other[2] >= end for other in ordered[i + 1 :])
