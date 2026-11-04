"""Made-up pools and leases, so the Resources page has something to show.

``qasight lease demo`` writes them into this machine's lease database, and the
static demo site (``scripts/export_static_site.py --demo-resources``) freezes
them. Nothing here is real: the "VMs", "phones" and holders are invented, and
the leases live a week so the page stays filled while you look around.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from qasight.resources.pools import ResourceItem, ResourcePool
from qasight.resources.store import Holder, LeaseStore

# Long enough to look around; ``qasight lease demo --clear`` gives the keys back sooner.
DEMO_LEASE_TTL_S = 7 * 24 * 3600.0


def _vm(n: int, os_label: str) -> ResourceItem:
    return ResourceItem(
        id=f"demo-vm-{n:02d}",
        labels=(os_label,),
        data=(("address", f"10.0.0.{10 + n}"), ("snapshot", "clean")),
    )


DEMO_POOLS: tuple[ResourcePool, ...] = (
    ResourcePool(
        name="demo-flow-vms",
        items=(
            _vm(1, "win11"),
            _vm(2, "win11"),
            _vm(3, "win11"),
            _vm(4, "ubuntu"),
            _vm(5, "ubuntu"),
            _vm(6, "ubuntu"),
        ),
        lease_ttl_s=DEMO_LEASE_TTL_S,
        max_per_user=4,
    ),
    ResourcePool(
        name="demo-phones",
        items=(
            ResourceItem(id="demo-pixel-8", labels=("android",), data=(("serial", "PX8-0001"),)),
            ResourceItem(id="demo-iphone-15", labels=("ios",), data=(("udid", "00008110-DEMO"),)),
        ),
        lease_ttl_s=DEMO_LEASE_TTL_S,
    ),
    ResourcePool(
        name="demo-license-seats",
        items=tuple(ResourceItem(id=f"demo-license-seats-{n}") for n in (1, 2, 3)),
        lease_ttl_s=DEMO_LEASE_TTL_S,
    ),
)

# (pool, labels, holder) in the order they take keys: each gets the first free match.
_TAKES: tuple[tuple[str, tuple[str, ...], Holder], ...] = (
    (
        "demo-flow-vms",
        ("win11",),
        Holder(user="alice", host="alice-laptop", pid=4211, stage="flows/checkout", worker="gw0"),
    ),
    (
        "demo-flow-vms",
        ("win11",),
        Holder(user="alice", host="alice-laptop", pid=4212, stage="flows/checkout", worker="gw1"),
    ),
    (
        "demo-flow-vms",
        ("ubuntu",),
        Holder(
            user="ci",
            host="ci-runner-3",
            pid=901,
            stage="nightly/flows",
            worker="gw0",
            note="nightly run",
        ),
    ),
    (
        "demo-phones",
        ("android",),
        Holder(user="bob", host="bob-mac", pid=7730, stage="mobile/smoke", worker="main"),
    ),
    ("demo-license-seats", (), Holder(user="ci", host="ci-runner-1", pid=120, stage="pr/ui")),
    ("demo-license-seats", (), Holder(user="alice", host="alice-laptop", pid=4300, stage="pr/ui")),
    ("demo-license-seats", (), Holder(user="bob", host="bob-mac", pid=7800, stage="pr/ui")),
)
# A key that "broke" and was taken out of the pool.
_DISABLED = ("demo-flow-vms", "demo-vm-05", "snapshot 'clean' is missing", "alice")
# People queued for a license seat (waiters drop out after about 30 s unless they keep asking).
_WAITERS: tuple[Holder, ...] = (
    Holder(user="ci", host="ci-runner-2", pid=130, stage="pr/ui"),
    Holder(user="bob", host="bob-mac", pid=5150, stage="pr/ui"),
)


def _waiter_id(n: int) -> str:
    return f"demo-waiter-{n + 1}"


@dataclass(frozen=True)
class DemoResult:
    held: int
    disabled: int
    waiting: int


def clear_demo(store: LeaseStore) -> int:
    """Give back every demo key and put the broken one back. Returns how many were held."""
    freed = 0
    for pool in DEMO_POOLS:
        for item in pool.items:
            freed += bool(store.force_release(pool.name, item.id))
    for n in range(len(_WAITERS)):
        store.leave(_waiter_id(n))
    pool_name, item_id, _reason, _by = _DISABLED
    store.set_disabled(pool_name, item_id, disabled=False)
    return freed


def seed_demo(store: LeaseStore, *, now: float | None = None) -> DemoResult:
    """Fill the demo pools: some keys held, one disabled, two requests waiting.

    Starts from a clean slate each time (``clear_demo``), so running it twice
    gives the same picture.
    """
    now = time.time() if now is None else now
    pools = {pool.name: pool for pool in DEMO_POOLS}
    clear_demo(store)
    held = 0
    for offset, (pool_name, labels, holder) in enumerate(_TAKES):
        # Taken a few minutes apart, oldest first, so "taken" times differ on the page.
        at = now - 60.0 * (len(_TAKES) - offset) * 7
        result = store.take(pools[pool_name], labels=labels, holder=holder, now=at)
        if result.lease is not None:
            held += 1
            # Holders that look alive: each heartbeat a few seconds ago.
            store.heartbeat(result.lease.lease_id, now=now - 3.0 * (offset + 1))
        if offset == 0:  # the pool's keys are known now, so the broken one can be disabled
            pool_name_d, item_id, reason, by = _DISABLED
            store.set_disabled(pool_name_d, item_id, disabled=True, reason=reason, by=by)
    waiting = 0
    for n, holder in enumerate(_WAITERS):
        result = store.take(
            pools["demo-license-seats"], holder=holder, waiter_id=_waiter_id(n), now=now
        )
        waiting += result.lease is None
    return DemoResult(held=held, disabled=1, waiting=waiting)
