"""Leases on pool keys: exclusive, expiring, fair (qasight/resources/store.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlmodel import create_engine

from qasight.resources.pools import parse_pool
from qasight.resources.store import FORGET_AFTER_S, Holder, LeaseStore


@pytest.fixture
def store(tmp_path: Path) -> LeaseStore:
    return LeaseStore(create_engine(f"sqlite:///{tmp_path / 'history.db'}"))


VMS = parse_pool("vms", {"items": ["vm-1", {"id": "vm-2", "labels": ["win"]}], "lease_ttl": 10})


def test_a_key_goes_to_one_holder_until_it_is_released(store: LeaseStore) -> None:
    a = store.take(VMS, holder=Holder(user="a"), now=100).lease
    b = store.take(VMS, holder=Holder(user="b"), now=100).lease
    assert a is not None and b is not None
    assert {a.item, b.item} == {"vm-1", "vm-2"}
    none = store.take(VMS, holder=Holder(user="c"), now=100)
    assert (none.lease, none.reason, none.free) == (None, "busy", 0)

    assert store.release(a.lease_id) is True
    assert store.release(a.lease_id) is False
    again = store.take(VMS, holder=Holder(user="c"), now=101).lease
    assert again is not None and again.item == a.item


def test_a_holder_that_stops_heartbeating_loses_the_key(store: LeaseStore) -> None:
    lease = store.take(VMS, labels=["win"], holder=Holder(user="a"), now=100).lease
    assert lease is not None and lease.expires_at == 110
    kept = store.heartbeat(lease.lease_id, now=108)
    assert kept is not None and kept.expires_at == 118
    # Past expires_at the old holder can't revive it, and after the reuse delay
    # (lease_ttl / 3 by default) the key is free for the next taker.
    assert store.heartbeat(lease.lease_id, now=119) is None
    taken = store.take(VMS, labels=["win"], holder=Holder(user="b"), now=122).lease
    assert taken is not None and taken.item == "vm-2" and taken.lease_id != lease.lease_id
    assert store.heartbeat(lease.lease_id, now=200) is None


def test_keys_go_to_waiters_first_come_first_served(store: LeaseStore) -> None:
    one = parse_pool("one", {"items": ["k"], "lease_ttl": 10})
    held = store.take(one, holder=Holder(user="a"), now=100).lease
    assert held is not None
    assert store.take(one, holder=Holder(user="early"), waiter_id="early", now=101).reason == "busy"
    assert store.take(one, holder=Holder(user="late"), waiter_id="late", now=102).reason == "busy"
    store.release(held.lease_id)

    # The later waiter asks first, but the earlier one is ahead of it.
    late = store.take(one, holder=Holder(user="late"), waiter_id="late", now=103)
    assert (late.lease, late.ahead) == (None, 1)
    early = store.take(one, holder=Holder(user="early"), waiter_id="early", now=103)
    assert early.lease is not None
    assert store.status("one", now=104)[0].waiting == 1


def test_a_waiter_for_other_keys_does_not_block(store: LeaseStore) -> None:
    store.take(VMS, labels=["win"], holder=Holder(user="a"), now=100)
    # Waits for vm-2 only (label win); vm-1 is free for someone who takes any key.
    assert (
        store.take(VMS, labels=["win"], holder=Holder(), waiter_id="w-win", now=101).reason
        == "busy"
    )
    other = store.take(VMS, holder=Holder(), waiter_id="w-any", now=102)
    assert other.lease is not None and other.lease.item == "vm-1"


def test_a_waiter_that_gives_up_leaves_the_queue(store: LeaseStore) -> None:
    one = parse_pool("one", {"items": ["k"]})
    held = store.take(one, holder=Holder(), now=100).lease
    assert held is not None
    store.take(one, holder=Holder(), waiter_id="gone", now=101)
    store.release(held.lease_id)
    # Its process died: no more asks, so after WAITER_TTL_S it no longer counts.
    assert store.take(one, holder=Holder(), waiter_id="next", now=110).reason == "busy"
    assert store.take(one, holder=Holder(), waiter_id="next", now=140).lease is not None


def test_max_per_user(store: LeaseStore) -> None:
    capped = parse_pool("capped", {"count": 3, "max_per_user": 2})
    for _ in range(2):
        assert store.take(capped, holder=Holder(user="dana"), now=100).lease is not None
    limited = store.take(capped, holder=Holder(user="dana"), waiter_id="w", now=100)
    assert (limited.lease, limited.reason, limited.free) == (None, "limit", 1)
    # Not in the queue while over the limit, so others don't wait behind it.
    assert store.take(capped, holder=Holder(user="ori"), now=100).lease is not None


def test_disabled_keys_are_skipped_and_can_come_back(store: LeaseStore) -> None:
    store.status(pools=[VMS])
    assert store.set_disabled("vms", "vm-2", disabled=True, reason="snapshot broken", by="ada")
    assert store.take(VMS, labels=["win"], holder=Holder(), now=100).reason == "no_items"
    item = next(i for i in store.status("vms")[0].items if i.item == "vm-2")
    assert (item.disabled, item.disabled_reason, item.disabled_by) == (
        True,
        "snapshot broken",
        "ada",
    )
    assert store.available(VMS) == 1

    assert store.set_disabled("vms", "vm-2", disabled=False)
    assert store.take(VMS, labels=["win"], holder=Holder(), now=100).lease is not None
    assert store.set_disabled("vms", "nope", disabled=True) is False


def test_an_admin_takes_a_key_back(store: LeaseStore) -> None:
    lease = store.take(VMS, holder=Holder(user="a"), now=100).lease
    assert lease is not None
    assert store.force_release("vms", lease.item) is True
    assert store.heartbeat(lease.lease_id, now=101) is None
    assert store.force_release("vms", lease.item) is False


def test_status_shows_holders_and_forgets_keys_nobody_describes(store: LeaseStore) -> None:
    lease = store.take(
        VMS, holder=Holder(user="a", host="laptop", run_id="r1", worker="gw0"), now=100
    ).lease
    assert lease is not None
    [pool] = store.status(now=101)
    assert (pool.name, pool.free, len(pool.items)) == ("vms", 1, 2)
    held = next(i for i in pool.items if i.lease)
    assert held.lease is not None
    assert (held.lease.holder.user, held.lease.holder.host, held.lease.holder.worker) == (
        "a",
        "laptop",
        "gw0",
    )

    # A key that left the config is hidden once it is free and a week old.
    later = 100 + FORGET_AFTER_S + 1
    smaller = parse_pool("vms", {"items": [lease.item]})
    store.take(smaller, holder=Holder(), now=later)  # describes the pool again (lease expired)
    assert [i.item for i in store.status("vms", now=later)[0].items] == [lease.item]


def test_an_expired_key_stays_out_for_its_reuse_delay(store: LeaseStore) -> None:
    one = parse_pool("one", {"items": ["k"], "lease_ttl": 10, "reuse_delay": 5})
    lease = store.take(one, holder=Holder(user="a"), now=100).lease
    assert lease is not None and lease.reuse_delay_s == 5
    # Expired at 110, but a frozen holder gets until 115 to notice before anyone else has it.
    assert store.take(one, holder=Holder(user="b"), now=112).reason == "busy"
    [item] = store.status("one", now=112)[0].items
    assert item.lease is not None and item.lease.expired(112)
    assert store.heartbeat(lease.lease_id, now=112) is None
    assert store.take(one, holder=Holder(user="b"), now=116).lease is not None


def test_the_reuse_delay_defaults_to_one_heartbeat() -> None:
    assert parse_pool("p", {"items": ["k"], "lease_ttl": 90}).reuse_delay == 30
    assert parse_pool("p", {"items": ["k"], "reuse_delay": 0}).reuse_delay == 0


def test_the_next_taker_learns_whether_the_key_was_left_clean(store: LeaseStore) -> None:
    one = parse_pool("one", {"items": ["k"], "lease_ttl": 10, "reuse_delay": 0})
    first = store.take(one, holder=Holder(), now=100).lease
    assert first is not None and first.clean is False  # never used: unknown, so not clean
    store.release(first.lease_id, clean=True)
    assert store.status("one", now=101)[0].items[0].clean is True
    second = store.take(one, holder=Holder(), now=101).lease
    assert second is not None and second.clean is True
    assert store.status("one", now=101)[0].items[0].clean is False
    store.release(second.lease_id)  # gave it back without resetting it
    third = store.take(one, holder=Holder(), now=102).lease
    assert third is not None and third.clean is False
    # A lease that expires never leaves the key clean, nor does one taken back.
    fourth = store.take(one, holder=Holder(), now=200).lease
    assert fourth is not None and fourth.clean is False
    assert store.force_release("one", "k")
    assert store.take(one, holder=Holder(), now=201).lease.clean is False  # type: ignore[union-attr]


def test_a_database_from_before_these_columns_gets_them(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE resource_item (pool VARCHAR(128) NOT NULL, item VARCHAR(128) NOT NULL,"
                " labels JSON NOT NULL, data JSON NOT NULL, disabled BOOLEAN NOT NULL,"
                " disabled_reason VARCHAR(500) NOT NULL, disabled_by VARCHAR(64),"
                " seen_at FLOAT NOT NULL, PRIMARY KEY (pool, item))"
            )
        )
        conn.execute(
            text("INSERT INTO resource_item VALUES ('one', 'k', '[]', '{}', 0, '', NULL, 100.0)")
        )
    store = LeaseStore(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("resource_item")}
    assert "clean" in columns
    LeaseStore(engine)  # a second worker starting finds nothing left to add
    one = parse_pool("one", {"items": ["k"]})
    lease = store.take(one, holder=Holder(), now=100).lease
    assert lease is not None and lease.clean is False
    assert store.release(lease.lease_id, clean=True)
