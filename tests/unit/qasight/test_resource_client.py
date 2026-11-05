"""Taking and holding a key: waiting, hooks, heartbeats (qasight/resources/client.py)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from sqlmodel import create_engine

from qasight.config.errors import ConfigValidationError
from qasight.resources.client import (
    LeaseTimeout,
    acquire,
    backend_from_env,
    default_holder,
    find_pool,
    lease_env,
    local_lease_db_url,
    spec_env,
)
from qasight.resources.pools import parse_pool
from qasight.resources.store import LeaseError, LeaseStore


@pytest.fixture
def store(tmp_path: Path) -> LeaseStore:
    return LeaseStore(create_engine(f"sqlite:///{tmp_path / 'history.db'}"))


def _ok(code: int) -> str:
    return f'"{sys.executable}" -c "import sys; sys.exit({code})"'


def test_the_worker_gets_its_key_in_env_vars(store: LeaseStore) -> None:
    pool = parse_pool(
        "vms", {"items": [{"id": "vm-2", "labels": ["win"], "data": {"ip-address": "10.0.0.2"}}]}
    )
    with acquire(store, pool, wait_s=0) as held:
        env = held.env()
        assert env["TESTO_RESOURCE_ID"] == "vm-2"
        assert env["TESTO_RESOURCE_POOL"] == "vms"
        assert env["TESTO_RESOURCE_LABELS"] == "win"
        assert json.loads(env["TESTO_RESOURCE_DATA"]) == {"ip-address": "10.0.0.2"}
        assert env["TESTO_RESOURCE_IP_ADDRESS"] == "10.0.0.2"
        assert env["TESTO_LEASE_ID"] == held.lease.lease_id
        assert lease_env(held.lease) == env
    # Released on exit.
    assert store.available(pool) == 1


def test_waiting_gives_up_after_the_limit(store: LeaseStore) -> None:
    pool = parse_pool("one", {"items": ["k"]})
    waits: list[int] = []
    with acquire(store, pool, wait_s=0):
        started = time.monotonic()
        with pytest.raises(LeaseTimeout, match="no key of pool 'one' came free in time"):
            acquire(store, pool, wait_s=0.5, poll_s=0.1, on_wait=lambda r: waits.append(r.free))
        assert time.monotonic() - started < 3
    assert waits and set(waits) == {0}
    # It left the queue, so the next request is not behind a ghost.
    assert store.status("one")[0].waiting == 0


def test_no_usable_key_fails_at_once(store: LeaseStore) -> None:
    pool = parse_pool("vms", {"items": ["vm-1"]})
    with pytest.raises(LeaseError, match="no usable key with labels \\['gpu'\\]"):
        acquire(store, pool, labels=["gpu"], wait_s=None)


def test_a_failing_on_take_hook_disables_the_key_and_tries_the_next(
    store: LeaseStore, tmp_path: Path
) -> None:
    # vm-1's hook fails, vm-2's passes: the hook sees the key in TESTO_RESOURCE_ID.
    script = tmp_path / "hook.py"
    script.write_text(
        "import os, sys\nsys.exit(1 if os.environ['TESTO_RESOURCE_ID'] == 'vm-1' else 0)\n"
    )
    pool = parse_pool(
        "vms", {"items": ["vm-1", "vm-2"], "on_take": f'"{sys.executable}" "{script}"'}
    )
    with acquire(store, pool, wait_s=0) as held:
        assert held.id == "vm-2"
    [status] = store.status("vms")
    broken = next(i for i in status.items if i.item == "vm-1")
    assert broken.disabled and broken.disabled_reason == "on_take failed: exit 1"


def test_a_failing_on_release_hook_disables_the_key(store: LeaseStore) -> None:
    pool = parse_pool("vms", {"items": ["vm-1"], "on_release": _ok(3)})
    with acquire(store, pool, wait_s=0):
        pass
    item = store.status("vms")[0].items[0]
    assert (item.lease, item.disabled, item.disabled_reason) == (
        None,
        True,
        "on_release failed: exit 3",
    )


def test_a_failing_on_check_hook_disables_the_key_and_tries_the_next(
    store: LeaseStore, tmp_path: Path
) -> None:
    # on_take "resets" every key; on_check then finds vm-1 unusable.
    script = tmp_path / "check.py"
    script.write_text(
        "import os, sys\nsys.exit(2 if os.environ['TESTO_RESOURCE_ID'] == 'vm-1' else 0)\n"
    )
    pool = parse_pool(
        "vms",
        {
            "items": ["vm-1", "vm-2"],
            "on_take": _ok(0),
            "on_check": f'"{sys.executable}" "{script}"',
        },
    )
    with acquire(store, pool, wait_s=0) as held:
        assert held.id == "vm-2"
    broken = next(i for i in store.status("vms")[0].items if i.item == "vm-1")
    assert broken.disabled and broken.disabled_reason == "on_check failed: exit 2"


def test_a_working_on_release_tells_the_next_taker_the_key_is_clean(
    store: LeaseStore, tmp_path: Path
) -> None:
    seen = tmp_path / "seen.txt"
    script = tmp_path / "record.py"
    script.write_text(
        f"import os\nopen({str(seen)!r}, 'a').write(os.environ['TESTO_RESOURCE_CLEAN'])\n"
    )
    record = f'"{sys.executable}" "{script}"'
    pool = parse_pool("vms", {"items": ["vm-1"], "on_take": record, "on_release": _ok(0)})
    with acquire(store, pool, wait_s=0) as held:
        assert held.env()["TESTO_RESOURCE_CLEAN"] == "0"
    with acquire(store, pool, wait_s=0) as held:
        assert held.env()["TESTO_RESOURCE_CLEAN"] == "1"
    # on_take saw it too, so it can skip a slow reset.
    assert seen.read_text() == "01"
    # Without an on_release nobody reset the key, so it is never clean.
    plain = parse_pool("seats", {"count": 1})
    for _ in range(2):
        with acquire(store, plain, wait_s=0) as held:
            assert held.env()["TESTO_RESOURCE_CLEAN"] == "0"


def test_the_heartbeat_keeps_a_short_lease_alive(store: LeaseStore) -> None:
    pool = parse_pool("vms", {"items": ["vm-1"], "lease_ttl": 1.5})
    with acquire(store, pool, wait_s=0) as held:
        time.sleep(3)
        assert not held.lost.is_set()
        assert store.available(pool) == 0
        # An admin takes it back: the next heartbeat notices.
        store.force_release("vms", "vm-1")
        assert held.lost.wait(3)


def test_holder_names_this_process() -> None:
    holder = default_holder(run_id="r1", worker="gw3")
    assert holder.pid > 0 and holder.host
    assert (holder.run_id, holder.worker) == ("r1", "gw3")


def test_find_pool_prefers_the_spec_a_stage_hands_down(tmp_path: Path) -> None:
    config = tmp_path / "qasight.yaml"
    config.write_text("resources:\n  vms: {items: [vm-1]}\n", encoding="utf-8")
    assert [i.id for i in find_pool("vms", config_path=config).items] == ["vm-1"]
    with pytest.raises(ConfigValidationError, match="no resource pool 'gpus'.*pools: vms"):
        find_pool("gpus", config_path=config)

    sent = parse_pool("gpus", {"count": 2})
    found = find_pool("gpus", env={"TESTO_RESOURCE_SPEC": spec_env(sent)})
    assert found == sent


def test_backend_choice_and_the_absolute_local_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.resources.client import RemoteLeases

    remote = backend_from_env(
        env={"TESTO_SERVER_URL": "https://qasight.example/", "TESTO_API_TOKEN": "t"}
    )
    assert isinstance(remote, RemoteLeases)
    assert remote.base == "https://qasight.example/api/v1/resources"
    assert remote.headers == {"Authorization": "Bearer t"}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./history.db")
    monkeypatch.delenv("TESTO_LEASE_DB_URL", raising=False)
    assert local_lease_db_url() == f"sqlite:///{tmp_path.resolve() / 'history.db'}"
    local = backend_from_env(env={"TESTO_LEASE_DB_URL": f"sqlite:///{tmp_path / 'x.db'}"})
    assert isinstance(local, LeaseStore)


def test_on_release_never_runs_on_a_key_someone_else_now_holds(
    store: LeaseStore, tmp_path: Path
) -> None:
    # P6 in docs/engine-cli-review.md: A's lease lapses between two heartbeats, B takes
    # the key, then A gives it back. A's on_release must not reset B's key.
    marker = tmp_path / "released.txt"
    script = tmp_path / "release.py"
    script.write_text(f"open({str(marker)!r}, 'a').write('reset')\n")
    pool = parse_pool(
        "vms", {"items": ["vm-1"], "lease_ttl": 30, "on_release": f'"{sys.executable}" "{script}"'}
    )
    a = acquire(store, pool, wait_s=0)
    store.force_release("vms", "vm-1")  # as if A's lease expired
    with acquire(store, pool, wait_s=0) as b:
        a.release()
        assert not marker.exists()
        assert store.available(pool) == 0 and b.id == "vm-1"
    assert marker.read_text() == "reset"  # B's own release still resets it
