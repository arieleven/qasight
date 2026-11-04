"""Resource pools on a team server: one place where every laptop and CI agent leases keys.

See :mod:`qasight.resources.store` for how a lease works. Reads are open
like the rest of the API (or need a session with ``TESTO_PROTECT_READS``) and
never show lease ids. Taking, heartbeating, giving back and disabling keys are
writes, so they need the API token or a ``runner`` account; taking a key back
from someone else (``/items/release``) needs ``admin``.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response

from qasight.config.errors import ConfigValidationError
from qasight.resources.pools import parse_labels, parse_pool
from qasight.resources.store import Holder, Lease, LeaseStore, PoolStatus, ProviderAction
from qasight_api.models import (
    ResourceActionItem,
    ResourceActionRequest,
    ResourceActionsResponse,
    ResourceHolder,
    ResourceItemRequest,
    ResourceItemStatus,
    ResourceLeaseInfo,
    ResourceLeaseItem,
    ResourcePoolStatus,
    ResourcesResponse,
    ResourceTakeRequest,
    ResourceTakeResponse,
)
from qasight_api.security import TOKEN_PRINCIPAL, current_principal

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])

_LEASE_ID = Path(pattern=r"^[0-9a-f]{32}$")
_WAITER_ID = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")


def _account(request: Request) -> str | None:
    """The signed-in account's name; None for the shared token (it names no one)."""
    principal = current_principal(request)
    return principal.username if principal and principal != TOKEN_PRINCIPAL else None


def _holder(holder: Holder) -> ResourceHolder:
    return ResourceHolder(
        user=holder.user,
        host=holder.host,
        pid=holder.pid,
        run_id=holder.run_id,
        stage=holder.stage,
        worker=holder.worker,
        note=holder.note,
    )


def lease_item(lease: Lease) -> ResourceLeaseItem:
    return ResourceLeaseItem(
        lease_id=lease.lease_id,
        pool=lease.pool,
        item=lease.item,
        labels=list(lease.labels),
        data=dict(lease.data),
        holder=_holder(lease.holder),
        taken_at=lease.taken_at,
        heartbeat_at=lease.heartbeat_at,
        expires_at=lease.expires_at,
        ttl_s=lease.ttl_s,
        reuse_delay_s=lease.reuse_delay_s,
        clean=lease.clean,
    )


def pool_status(pool: PoolStatus) -> ResourcePoolStatus:
    return ResourcePoolStatus(
        name=pool.name,
        free=pool.free,
        waiting=pool.waiting,
        items=[
            ResourceItemStatus(
                item=i.item,
                labels=list(i.labels),
                data=dict(i.data),
                disabled=i.disabled,
                disabled_reason=i.disabled_reason,
                disabled_by=i.disabled_by,
                lease=ResourceLeaseInfo(
                    holder=_holder(i.lease.holder),
                    taken_at=i.lease.taken_at,
                    heartbeat_at=i.lease.heartbeat_at,
                    expires_at=i.lease.expires_at,
                    ttl_s=i.lease.ttl_s,
                    reuse_delay_s=i.lease.reuse_delay_s,
                )
                if i.lease
                else None,
                clean=i.clean,
            )
            for i in pool.items
        ],
    )


@router.get("", response_model=ResourcesResponse)
def list_resources(pool: str | None = None) -> ResourcesResponse:
    """Every pool the server knows, with each key's holder and how many requests wait."""
    return ResourcesResponse(pools=[pool_status(p) for p in LeaseStore().status(pool)])


@router.post("/leases", response_model=ResourceTakeResponse)
def take_lease(body: ResourceTakeRequest, request: Request) -> ResourceTakeResponse:
    """Take a free key, or say how many requests are ahead. Ask again (same ``waiter_id``) to wait."""
    try:
        pool = parse_pool(body.pool, body.spec)
        labels = parse_labels(body.labels, where="labels")
    except ConfigValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    holder = body.holder
    result = LeaseStore().take(
        pool,
        labels=labels,
        holder=Holder(
            user=_account(request) or holder.user,
            host=holder.host,
            pid=holder.pid,
            run_id=holder.run_id,
            stage=holder.stage,
            worker=holder.worker,
            note=holder.note,
        ),
        waiter_id=body.waiter_id,
    )
    return ResourceTakeResponse(
        lease=lease_item(result.lease) if result.lease else None,
        ahead=result.ahead,
        free=result.free,
        reason=result.reason,  # type: ignore[arg-type]  # LeaseStore only returns these
    )


@router.post("/leases/{lease_id}/heartbeat", response_model=ResourceLeaseItem)
def heartbeat_lease(lease_id: str = _LEASE_ID) -> ResourceLeaseItem:
    """Keep a lease. 404 means it is gone: it expired, or an admin took the key back."""
    lease = LeaseStore().heartbeat(lease_id)
    if lease is None:
        raise HTTPException(status_code=404, detail="That lease is gone (expired or released).")
    return lease_item(lease)


@router.delete("/leases/{lease_id}", status_code=204)
def release_lease(lease_id: str = _LEASE_ID, clean: bool = False) -> Response:
    """Give a key back. ``clean=true``: the holder reset it, so the next taker may skip a reset."""
    if not LeaseStore().release(lease_id, clean=clean):
        raise HTTPException(status_code=404, detail="That lease is gone (expired or released).")
    return Response(status_code=204)


@router.delete("/waiters/{waiter_id}", status_code=204)
def leave_queue(waiter_id: str = _WAITER_ID) -> Response:
    """Stop waiting for a key."""
    LeaseStore().leave(waiter_id)
    return Response(status_code=204)


@router.post("/items/disable", status_code=204)
def disable_item(body: ResourceItemRequest, request: Request) -> Response:
    """Take a broken key out of its pool; nobody gets it until it is enabled again."""
    if not LeaseStore().set_disabled(
        body.pool, body.item, disabled=True, reason=body.reason, by=_account(request)
    ):
        raise HTTPException(status_code=404, detail="No such key.")
    return Response(status_code=204)


@router.post("/items/enable", status_code=204)
def enable_item(body: ResourceItemRequest) -> Response:
    """Put a key back in its pool."""
    if not LeaseStore().set_disabled(body.pool, body.item, disabled=False):
        raise HTTPException(status_code=404, detail="No such key.")
    return Response(status_code=204)


@router.post("/items/release", status_code=204)
def force_release_item(body: ResourceItemRequest) -> Response:
    """Take a key back from whoever holds it (admin). Their next heartbeat finds it gone."""
    if not LeaseStore().force_release(body.pool, body.item):
        raise HTTPException(status_code=404, detail="Nobody holds that key.")
    return Response(status_code=204)


@router.get("/actions", response_model=ResourceActionsResponse)
def list_actions(
    pool: str | None = None, limit: int = Query(default=50, ge=1, le=500)
) -> ResourceActionsResponse:
    """Provider commands people ran (``qasight pool add/remove/sweep --apply``), newest first."""
    return ResourceActionsResponse(
        actions=[
            ResourceActionItem(
                pool=a.pool,
                item=a.item,
                verb=a.verb,  # type: ignore[arg-type]  # only create/destroy are recorded
                user=a.user,
                host=a.host,
                command=a.command,
                ok=a.ok,
                detail=a.detail,
                at=a.at,
            )
            for a in LeaseStore().actions(pool, limit=limit)
        ]
    )


@router.post("/actions", status_code=204)
def record_action(body: ResourceActionRequest, request: Request) -> Response:
    """Log a provider command a client ran. The server never runs one itself."""
    LeaseStore().record_action(
        ProviderAction(
            pool=body.pool,
            item=body.item,
            verb=body.verb,
            user=_account(request) or body.user,
            host=body.host,
            command=body.command,
            ok=body.ok,
            detail=body.detail,
            # The server's clock, like every lease time.
            at=time.time(),
        )
    )
    return Response(status_code=204)
