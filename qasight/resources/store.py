"""Leases on resource pool keys, kept in the run-history database.

A lease is a lock with an owner and an expiry time:

- **Take**: one insert into ``resource_lease``, whose primary key is the key
  itself. A second holder's insert fails on every database, so two workers never
  get the same key, whether they run on one machine or on many laptops talking
  to one team server.
- **Heartbeat**: the holder pushes ``expires_at`` forward every few seconds. When
  a process dies (crash, ``kill -9``, a laptop that sleeps) the heartbeats stop,
  and the first take after ``expires_at`` plus the pool's ``reuse_delay`` deletes
  the row. No stale locks. The delay gives a holder that was only frozen time to
  find out (its next heartbeat is refused) before someone else gets the key.
- **Clean or not**: a key given back after a successful ``on_release`` is marked
  clean; the next taker sees that in :attr:`Lease.clean` (``TESTO_RESOURCE_CLEAN``)
  and may skip its own reset. A lease that expired or was taken back never is.
- **Fair queue**: a request that finds nothing free waits in ``resource_waiter``.
  A key goes to the oldest waiter that can use it; a later waiter only goes first
  when the ones ahead want other keys.
- **Broken keys**: ``disable`` takes a key out of the pool until ``enable``.

All times are this store's clock (epoch seconds), so on a team server only the
server's clock counts. Clients reach it through :mod:`qasight.resources.client`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from qasight.resources.pools import ResourceItem, ResourcePool

# A waiter that has not asked again for this long has given up (its process died).
WAITER_TTL_S: Final[float] = 30.0
# Keys not described by any client for this long, and free, drop off the Resources page.
FORGET_AFTER_S: Final[float] = 7 * 24 * 3600.0
_RESEEN_AFTER_S: Final[float] = 3600.0
# (database URL, table) pairs whose columns are known to be up to date.


@dataclass(frozen=True)
class Holder:
    """Who takes a key. ``user`` is the account on a team server with accounts."""

    user: str = ""
    host: str = ""
    pid: int = 0
    run_id: str = ""
    stage: str = ""
    worker: str = ""
    note: str = ""


@dataclass(frozen=True)
class Lease:
    lease_id: str
    pool: str
    item: str
    holder: Holder
    taken_at: float
    heartbeat_at: float
    expires_at: float
    ttl_s: float
    labels: tuple[str, ...] = ()
    data: tuple[tuple[str, str], ...] = ()
    # The previous holder gave the key back after a successful on_release.
    clean: bool = False
    reuse_delay_s: float = 0.0

    def expired(self, now: float) -> bool:
        """Heartbeats stopped; the key comes free at ``expires_at + reuse_delay_s``."""
        return self.expires_at < now


@dataclass(frozen=True)
class TakeResult:
    """A lease, or why there is none yet: ``ahead`` waiters and ``free`` keys."""

    lease: Lease | None
    ahead: int = 0
    free: int = 0
    # "busy" (wait and ask again), "limit" (max_per_user reached),
    # "no_items" (no enabled key matches; waiting won't help).
    reason: str | None = None


@dataclass(frozen=True)
class ItemStatus:
    pool: str
    item: str
    labels: tuple[str, ...]
    data: tuple[tuple[str, str], ...]
    disabled: bool
    disabled_reason: str
    disabled_by: str | None
    lease: Lease | None
    clean: bool = False


@dataclass(frozen=True)
class PoolStatus:
    name: str
    items: tuple[ItemStatus, ...]
    waiting: int = 0
    free: int = field(init=False)

    def __post_init__(self) -> None:
        free = sum(1 for i in self.items if i.lease is None and not i.disabled)
        object.__setattr__(self, "free", free)


@dataclass(frozen=True)
class ProviderAction:
    """A ``create`` or ``destroy`` command someone ran with ``qasight pool ... --apply``."""

    pool: str
    item: str
    verb: str
    user: str
    host: str
    command: str
    ok: bool
    detail: str = ""
    at: float = 0.0


class LeaseError(RuntimeError):
    """A lease could not be taken, kept or given back (the message says why)."""


class LeaseStore:
    """Leases on a SQLAlchemy engine (the run-history DB by default)."""

    def __init__(self, engine: Any = None) -> None:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        self._engine = engine if engine is not None else get_engine()
        # Migrations own the schema (and add the columns this table gained over time).
        create_db_and_tables(self._engine)

    # -- taking and giving back -------------------------------------------------

    def take(
        self,
        pool: ResourcePool,
        *,
        labels: Iterable[str] = (),
        holder: Holder,
        waiter_id: str | None = None,
        now: float | None = None,
    ) -> TakeResult:
        """Take a free key of ``pool`` with ``labels``, or say why not.

        Pass the same ``waiter_id`` on every try while waiting; it keeps the
        caller's place in the queue. Without one, the call never jumps a queue.
        """
        from sqlalchemy.exc import IntegrityError
        from sqlmodel import Session, col, delete, select

        from qasight.repository.models import ResourceItemRow, ResourceLease, ResourceWaiter

        now = time.time() if now is None else now
        wanted = tuple(labels)
        self._register(pool, now=now)
        with Session(self._engine) as session:
            session.exec(
                delete(ResourceLease).where(
                    col(ResourceLease.pool) == pool.name,
                    col(ResourceLease.expires_at) + col(ResourceLease.reuse_delay_s) < now,
                )
            )
            session.exec(
                delete(ResourceWaiter).where(
                    col(ResourceWaiter.pool) == pool.name,
                    col(ResourceWaiter.heartbeat_at) < now - WAITER_TTL_S,
                )
            )
            disabled = set(
                session.exec(
                    select(ResourceItemRow.item).where(
                        col(ResourceItemRow.pool) == pool.name,
                        col(ResourceItemRow.disabled) == True,  # noqa: E712 — SQL, not Python
                    )
                ).all()
            )
            candidates = [i for i in pool.matching(wanted) if i.id not in disabled]
            if not candidates:
                session.commit()
                return TakeResult(lease=None, reason="no_items")
            leases = session.exec(
                select(ResourceLease).where(col(ResourceLease.pool) == pool.name)
            ).all()
            held = {lease.item for lease in leases}
            free = [i for i in candidates if i.id not in held]

            me = session.get(ResourceWaiter, waiter_id) if waiter_id else None
            if pool.max_per_user and holder.user:
                mine = sum(1 for lease in leases if lease.holder_user == holder.user)
                if mine >= pool.max_per_user:
                    # Not in the queue while over the limit, so nobody waits behind it.
                    if me is not None:
                        session.delete(me)
                    session.commit()
                    return TakeResult(lease=None, free=len(free), reason="limit")
            if waiter_id and me is None:
                me = ResourceWaiter(
                    id=waiter_id[:64],
                    pool=pool.name,
                    candidates=[i.id for i in candidates],
                    holder_user=holder.user[:64],
                    created_at=now,
                )
                session.add(me)
            if me is not None:
                me.heartbeat_at = now
            # Waiters ahead of this one that want one of the free keys get them first.
            place = (me.created_at, me.id) if me is not None else (float("inf"), "")
            free_ids = {i.id for i in free}
            ahead = sum(
                1
                for w in session.exec(
                    select(ResourceWaiter).where(
                        col(ResourceWaiter.pool) == pool.name,
                        col(ResourceWaiter.id) != (waiter_id or ""),
                    )
                ).all()
                if (w.created_at, w.id) < place and free_ids & set(w.candidates)
            )
            session.commit()

        if len(free) <= ahead:
            return TakeResult(lease=None, ahead=ahead, free=len(free), reason="busy")

        for item in free:
            lease_id = uuid.uuid4().hex
            row = ResourceLease(
                pool=pool.name,
                item=item.id,
                lease_id=lease_id,
                holder_user=holder.user[:64],
                holder_host=holder.host[:255],
                holder_pid=int(holder.pid),
                run_id=holder.run_id[:128],
                stage=holder.stage[:128],
                worker=holder.worker[:64],
                note=holder.note[:200],
                taken_at=now,
                heartbeat_at=now,
                expires_at=now + pool.lease_ttl_s,
                ttl_s=pool.lease_ttl_s,
                reuse_delay_s=pool.reuse_delay,
            )
            with Session(self._engine) as session:
                session.add(row)
                try:
                    session.commit()
                except IntegrityError:  # someone else took it a moment ago
                    session.rollback()
                    continue
                if waiter_id:
                    session.exec(delete(ResourceWaiter).where(col(ResourceWaiter.id) == waiter_id))
                # Whether the last holder left it clean; from now on it is in use.
                item_row = session.get(ResourceItemRow, (pool.name, item.id))
                clean = bool(item_row is not None and item_row.clean)
                if item_row is not None and item_row.clean:
                    item_row.clean = False
                    session.add(item_row)
                session.commit()
                lease = _lease(row, item, clean=clean)
                return TakeResult(lease=lease, ahead=ahead, free=len(free) - 1)
        return TakeResult(lease=None, ahead=ahead, free=0, reason="busy")

    def heartbeat(self, lease_id: str, *, now: float | None = None) -> Lease | None:
        """Keep a lease; None when it is gone (expired, released or taken back)."""
        from sqlmodel import Session, col, select

        from qasight.repository.models import ResourceLease

        now = time.time() if now is None else now
        with Session(self._engine) as session:
            row = session.exec(
                select(ResourceLease).where(
                    col(ResourceLease.lease_id) == lease_id, col(ResourceLease.expires_at) >= now
                )
            ).first()
            if row is None:
                return None
            row.heartbeat_at = now
            row.expires_at = now + row.ttl_s
            session.add(row)
            session.commit()
            session.refresh(row)
            return _lease(row, None)

    def release(self, lease_id: str, *, clean: bool = False) -> bool:
        """Give a key back; False if the lease was already gone.

        ``clean=True`` says the holder reset the key (its ``on_release`` worked), so
        the next taker can skip a reset. Both happen in one transaction: nobody can
        take the key between the release and the mark.
        """
        from sqlmodel import Session, col, select

        from qasight.repository.models import ResourceItemRow, ResourceLease

        with Session(self._engine) as session:
            row = session.exec(
                select(ResourceLease).where(col(ResourceLease.lease_id) == lease_id)
            ).first()
            if row is None:
                return False
            item_row = session.get(ResourceItemRow, (row.pool, row.item))
            if item_row is not None and item_row.clean != clean:
                item_row.clean = clean
                session.add(item_row)
            session.delete(row)
            session.commit()
            return True

    def leave(self, waiter_id: str) -> None:
        """Stop waiting (gave up, or got a key some other way)."""
        from sqlmodel import Session, col, delete

        from qasight.repository.models import ResourceWaiter

        with Session(self._engine) as session:
            session.exec(delete(ResourceWaiter).where(col(ResourceWaiter.id) == waiter_id))
            session.commit()

    def available(self, pool: ResourcePool, *, labels: Iterable[str] = ()) -> int:
        """How many keys with ``labels`` are free and enabled right now."""
        wanted = {i.id for i in pool.matching(tuple(labels))}
        busy = {i.item for s in self.status(pool.name) for i in s.items if i.lease or i.disabled}
        return len(wanted - busy)

    # -- admin --------------------------------------------------------------------

    def set_disabled(
        self, pool: str, item: str, *, disabled: bool, reason: str = "", by: str | None = None
    ) -> bool:
        """Take a key out of its pool (``disabled=True``) or put it back. False if unknown."""
        from sqlmodel import Session

        from qasight.repository.models import ResourceItemRow

        with Session(self._engine) as session:
            row = session.get(ResourceItemRow, (pool, item))
            if row is None:
                return False
            row.disabled = disabled
            row.disabled_reason = reason.strip()[:500] if disabled else ""
            row.disabled_by = (by or None) if disabled else None
            session.add(row)
            session.commit()
            return True

    def force_release(self, pool: str, item: str) -> bool:
        """Take a key back from whoever holds it (their next heartbeat finds it gone)."""
        from sqlmodel import Session, col, delete

        from qasight.repository.models import ResourceLease

        with Session(self._engine) as session:
            result = session.exec(
                delete(ResourceLease).where(
                    col(ResourceLease.pool) == pool, col(ResourceLease.item) == item
                )
            )
            session.commit()
            return bool(result.rowcount)

    # -- reading ------------------------------------------------------------------

    def status(
        self,
        pool: str | None = None,
        *,
        pools: Sequence[ResourcePool] = (),
        now: float | None = None,
    ) -> list[PoolStatus]:
        """Every known pool (or one) with its keys, holders and queue length.

        ``pools`` are described first, so a pool from the local config shows up
        before anyone has taken a key from it.
        """
        from sqlmodel import Session, col, select

        from qasight.repository.models import ResourceItemRow, ResourceLease, ResourceWaiter

        now = time.time() if now is None else now
        for p in pools:
            self._register(p, now=now)
        with Session(self._engine) as session:
            items_q = select(ResourceItemRow)
            # A lease past expires_at still keeps its key until its reuse delay is over.
            leases_q = select(ResourceLease).where(
                col(ResourceLease.expires_at) + col(ResourceLease.reuse_delay_s) >= now
            )
            waiters_q = select(ResourceWaiter).where(
                col(ResourceWaiter.heartbeat_at) >= now - WAITER_TTL_S
            )
            if pool is not None:
                items_q = items_q.where(col(ResourceItemRow.pool) == pool)
                leases_q = leases_q.where(col(ResourceLease.pool) == pool)
                waiters_q = waiters_q.where(col(ResourceWaiter.pool) == pool)
            rows = session.exec(items_q).all()
            leases = {(r.pool, r.item): r for r in session.exec(leases_q).all()}
            waiters = session.exec(waiters_q).all()

        by_pool: dict[str, list[ItemStatus]] = {}
        for row in sorted(rows, key=lambda r: (r.pool, _natural(r.item))):
            lease_row = leases.get((row.pool, row.item))
            forgotten = row.seen_at < now - FORGET_AFTER_S
            if forgotten and lease_row is None and not row.disabled:
                continue
            labels, data = tuple(row.labels or ()), tuple((row.data or {}).items())
            by_pool.setdefault(row.pool, []).append(
                ItemStatus(
                    pool=row.pool,
                    item=row.item,
                    labels=labels,
                    data=data,
                    disabled=row.disabled,
                    disabled_reason=row.disabled_reason,
                    disabled_by=row.disabled_by,
                    lease=_lease(lease_row, ResourceItem(row.item, labels, data))
                    if lease_row
                    else None,
                    clean=bool(row.clean),
                )
            )
        return [
            PoolStatus(
                name=name,
                items=tuple(items),
                waiting=sum(1 for w in waiters if w.pool == name),
            )
            for name, items in sorted(by_pool.items())
        ]

    # -- the provider log ------------------------------------------------------

    def record_action(self, action: ProviderAction) -> None:
        """Log a provider command a person ran (``qasight pool ... --apply``)."""
        from sqlmodel import Session

        from qasight.repository.models import ResourceAction

        with Session(self._engine) as session:
            session.add(
                ResourceAction(
                    at=action.at or time.time(),
                    pool=action.pool,
                    item=action.item,
                    verb=action.verb,
                    user=action.user[:64],
                    host=action.host[:255],
                    command=action.command[:500],
                    ok=action.ok,
                    detail=action.detail[:500],
                )
            )
            session.commit()

    def actions(self, pool: str | None = None, *, limit: int = 50) -> list[ProviderAction]:
        """The newest provider commands first."""
        from sqlmodel import Session, col, select

        from qasight.repository.models import ResourceAction

        query = select(ResourceAction).order_by(col(ResourceAction.at).desc()).limit(limit)
        if pool is not None:
            query = query.where(col(ResourceAction.pool) == pool)
        with Session(self._engine) as session:
            rows = session.exec(query).all()
        return [
            ProviderAction(
                pool=r.pool,
                item=r.item,
                verb=r.verb,
                user=r.user,
                host=r.host,
                command=r.command,
                ok=r.ok,
                detail=r.detail,
                at=r.at,
            )
            for r in rows
        ]

    def _register(self, pool: ResourcePool, *, now: float) -> None:
        """Remember ``pool``'s keys (labels and data as the caller describes them)."""
        from sqlalchemy.exc import IntegrityError
        from sqlmodel import Session, col, select

        from qasight.repository.models import ResourceItemRow

        with Session(self._engine) as session:
            rows = {
                r.item: r
                for r in session.exec(
                    select(ResourceItemRow).where(col(ResourceItemRow.pool) == pool.name)
                ).all()
            }
            for item in pool.items:
                row = rows.get(item.id)
                if row is None:
                    row = ResourceItemRow(pool=pool.name, item=item.id)
                elif (
                    row.labels == list(item.labels)
                    and row.data == dict(item.data)
                    and row.seen_at >= now - _RESEEN_AFTER_S
                ):
                    continue  # every take describes the pool; only write what changed
                row.labels = list(item.labels)
                row.data = dict(item.data)
                row.seen_at = now
                session.add(row)
            try:
                session.commit()
            except IntegrityError:  # two clients described a new pool at once; one is enough
                session.rollback()


def _lease(row: Any, item: ResourceItem | None, *, clean: bool = False) -> Lease:
    return Lease(
        lease_id=row.lease_id,
        pool=row.pool,
        item=row.item,
        holder=Holder(
            user=row.holder_user,
            host=row.holder_host,
            pid=row.holder_pid,
            run_id=row.run_id,
            stage=row.stage,
            worker=row.worker,
            note=row.note,
        ),
        taken_at=row.taken_at,
        heartbeat_at=row.heartbeat_at,
        expires_at=row.expires_at,
        ttl_s=row.ttl_s,
        labels=item.labels if item else (),
        data=item.data if item else (),
        clean=clean,
        reuse_delay_s=float(row.reuse_delay_s or 0.0),
    )


def _natural(value: str) -> tuple[Any, ...]:
    """Sort ``vm-2`` before ``vm-10``."""
    import re

    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", value))
