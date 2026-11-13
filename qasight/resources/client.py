"""Take a key from a pool and hold it: the side every worker, CLI and stage uses.

Where the leases live:

- ``TESTO_SERVER_URL`` set (or ``server=``): on that team server, over HTTPS with
  ``TESTO_API_TOKEN``. This is what lets many laptops and CI agents share one pool.
- Otherwise: in this machine's run-history database (``TESTO_LEASE_DB_URL``, else
  the usual ``DATABASE_URL`` / config / SQLite file). Enough for parallel workers
  on one machine.

:func:`acquire` waits in the pool's fair queue, runs the pool's optional
``on_take`` and then ``on_check`` commands, and returns a :class:`HeldLease` that
heartbeats in a background thread until it is released. If a hook fails, the key
is taken out of the pool and another one is tried.

Hooks are the user's own code; QASight only runs them. Each one should be
safe to run again on a key in any state ("already reset" is success), because a
crash can stop any of them halfway.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import random
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final, Protocol

from qasight.resources.pools import ResourcePool, parse_pool
from qasight.resources.store import (
    Holder,
    ItemStatus,
    Lease,
    LeaseError,
    LeaseStore,
    PoolStatus,
    ProviderAction,
    TakeResult,
)

logger = logging.getLogger(__name__)

SERVER_URL_ENV: Final[str] = "TESTO_SERVER_URL"
TOKEN_ENV: Final[str] = "TESTO_API_TOKEN"
LEASE_DB_ENV: Final[str] = "TESTO_LEASE_DB_URL"
# A stage hands its pool to its workers as JSON, so they need no config file.
SPEC_ENV: Final[str] = "TESTO_RESOURCE_SPEC"
_HTTP_TIMEOUT_S: Final[float] = 15.0
_POLL_S: Final[float] = 2.0


class LeaseTimeout(LeaseError):
    """No key came free within the wait limit."""


class LeaseBackend(Protocol):
    """A :class:`~qasight.resources.store.LeaseStore`, here or on a server."""

    def take(
        self,
        pool: ResourcePool,
        *,
        labels: Iterable[str] = (),
        holder: Holder,
        waiter_id: str | None = None,
    ) -> TakeResult: ...

    def heartbeat(self, lease_id: str) -> Lease | None: ...

    def release(self, lease_id: str, *, clean: bool = False) -> bool: ...

    def leave(self, waiter_id: str) -> None: ...

    def available(self, pool: ResourcePool, *, labels: Iterable[str] = ()) -> int: ...

    def set_disabled(
        self, pool: str, item: str, *, disabled: bool, reason: str = "", by: str | None = None
    ) -> bool: ...

    def force_release(self, pool: str, item: str) -> bool: ...

    def status(
        self, pool: str | None = None, *, pools: Sequence[ResourcePool] = ()
    ) -> list[PoolStatus]: ...

    def record_action(self, action: ProviderAction) -> None: ...

    def actions(self, pool: str | None = None, *, limit: int = 50) -> list[ProviderAction]: ...


def backend_from_env(
    *, server: str | None = None, token: str | None = None, env: Mapping[str, str] | None = None
) -> LeaseBackend:
    source = os.environ if env is None else env
    server = server or source.get(SERVER_URL_ENV) or None
    if server:
        return RemoteLeases(server, token or source.get(TOKEN_ENV) or None)
    url = (source.get(LEASE_DB_ENV) or "").strip()
    if url:
        from qasight.repository.db_config import _build_engine

        return LeaseStore(_build_engine(url))
    return LeaseStore()


def local_lease_db_url() -> str:
    """This machine's lease database as an absolute URL, for child processes in other folders."""
    from pathlib import Path

    from qasight.repository.db_config import resolve_database_url

    url = (os.environ.get(LEASE_DB_ENV) or "").strip() or resolve_database_url()
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith(prefix + "/") and ":memory:" not in url:
        url = prefix + str(Path(url[len(prefix) :]).resolve())
    return url


def default_holder(**fields: Any) -> Holder:
    """This process as a holder: OS user, host name and pid, plus ``fields``."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 — no user name on some containers
        user = ""
    values: dict[str, Any] = {"user": user, "host": socket.gethostname(), "pid": os.getpid()}
    values.update({k: v for k, v in fields.items() if v is not None})
    return Holder(**values)


def lease_env(lease: Lease) -> dict[str, str]:
    """What a worker sees: ``TESTO_RESOURCE_ID`` and friends, one ``TESTO_RESOURCE_<NAME>`` per data value."""
    env = {
        "TESTO_RESOURCE_ID": lease.item,
        "TESTO_RESOURCE_POOL": lease.pool,
        "TESTO_RESOURCE_LABELS": ",".join(lease.labels),
        "TESTO_RESOURCE_DATA": json.dumps(dict(lease.data), sort_keys=True),
        # 1: the last holder reset this key (its on_release worked), so on_take may skip a reset.
        "TESTO_RESOURCE_CLEAN": "1" if lease.clean else "0",
        "TESTO_LEASE_ID": lease.lease_id,
    }
    for key, value in lease.data:
        name = "".join(c if c.isalnum() else "_" for c in key.upper())
        env.setdefault(f"TESTO_RESOURCE_{name}", value)
    return env


class HeldLease:
    """A key this process holds. Heartbeats until :meth:`release`; use it as a context manager."""

    def __init__(self, backend: LeaseBackend, pool: ResourcePool, lease: Lease) -> None:
        self.backend = backend
        self.pool = pool
        self.lease = lease
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def id(self) -> str:
        return self.lease.item

    @property
    def data(self) -> dict[str, str]:
        return dict(self.lease.data)

    def env(self) -> dict[str, str]:
        return lease_env(self.lease)

    def start_heartbeat(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._beat, name=f"testo-lease-{self.lease.item}", daemon=True
        )
        self._thread.start()

    def _beat(self) -> None:
        # Three beats per TTL; a 1 s floor would leave a 1.5 s lease only half a second of slack.
        interval = max(0.2, self.lease.ttl_s / 3)
        while not self._stop.wait(interval):
            try:
                if self.backend.heartbeat(self.lease.lease_id) is None:
                    logger.warning(
                        "lost the lease on %s/%s (it expired or was taken back)",
                        self.lease.pool,
                        self.lease.item,
                    )
                    self.lost.set()
                    return
            except Exception:  # noqa: BLE001 — a blip; the TTL leaves room for the next try
                logger.warning("lease heartbeat for %s failed", self.lease.item, exc_info=True)

    def release(self) -> None:
        """Run ``on_release``, then give the key back (clean if it worked). Safe to call twice."""
        if self._stop.is_set():
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        clean = False
        if self.pool.on_release and not self.lost.is_set() and not self._still_ours():
            logger.warning(
                "skipping on_release for %s/%s: the lease expired, so the key may be someone "
                "else's now",
                self.lease.pool,
                self.lease.item,
            )
            self.lost.set()
        if self.pool.on_release and not self.lost.is_set():
            problem = run_hook(self.pool.on_release, self.lease, timeout_s=self.pool.hook_timeout_s)
            if problem:
                _disable_quietly(self.backend, self.lease, f"on_release failed: {problem}")
            clean = problem is None
        try:
            self.backend.release(self.lease.lease_id, clean=clean)
        except Exception:  # noqa: BLE001 — the TTL frees it anyway
            logger.warning("could not release %s; it frees itself after its TTL", self.lease.item)

    def _still_ours(self) -> bool:
        """One heartbeat right before ``on_release``: the background beat only checks every
        ttl/3, so a lease can expire (and the key be taken) between two beats."""
        try:
            return self.backend.heartbeat(self.lease.lease_id) is not None
        except Exception:  # noqa: BLE001 — can't tell; resetting someone else's key is worse
            logger.warning("could not check the lease on %s", self.lease.item, exc_info=True)
            return False

    def __enter__(self) -> HeldLease:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire(
    backend: LeaseBackend,
    pool: ResourcePool,
    *,
    labels: Iterable[str] = (),
    holder: Holder | None = None,
    wait_s: float | None = None,
    on_wait: Callable[[TakeResult], None] | None = None,
    poll_s: float = _POLL_S,
    keep_alive: bool = True,
) -> HeldLease:
    """Wait for a key of ``pool`` (``wait_s=None`` waits forever, ``0`` tries once).

    ``keep_alive=False`` skips the heartbeat thread, for a caller that heartbeats itself.
    """
    import uuid

    wanted = tuple(labels)
    holder = holder or default_holder()
    waiter_id = uuid.uuid4().hex
    deadline = None if wait_s is None else time.monotonic() + wait_s
    granted = False
    try:
        while True:
            result = backend.take(pool, labels=wanted, holder=holder, waiter_id=waiter_id)
            if result.lease is not None:
                lease = _with_item(pool, result.lease)
                problem = _prepare(pool, lease)
                if problem:
                    _disable_quietly(backend, lease, problem)
                    backend.release(lease.lease_id)
                    continue
                held = HeldLease(backend, pool, lease)
                if keep_alive:
                    held.start_heartbeat()
                granted = True
                return held
            if result.reason == "no_items":
                wanted_text = f" with labels {list(wanted)}" if wanted else ""
                raise LeaseError(
                    f"pool {pool.name!r} has no usable key{wanted_text} "
                    "(none match, or all are disabled)."
                )
            if deadline is not None and time.monotonic() >= deadline:
                raise LeaseTimeout(
                    f"no key of pool {pool.name!r} came free in time "
                    f"({result.free} free, {result.ahead} waiting ahead)."
                )
            if on_wait is not None:
                on_wait(result)
            pause = poll_s * random.uniform(0.75, 1.25)  # noqa: S311 — jitter, not crypto
            if deadline is not None:
                pause = max(0.0, min(pause, deadline - time.monotonic()))
            time.sleep(pause)
    finally:
        if not granted:
            try:
                backend.leave(waiter_id)
            except Exception:  # noqa: BLE001 — the waiter row expires anyway
                logger.debug("could not leave the queue", exc_info=True)


def _prepare(pool: ResourcePool, lease: Lease) -> str | None:
    """Run ``on_take`` (reset), then ``on_check`` (is it usable?); why the key is unusable, or None."""
    for name, command in (("on_take", pool.on_take), ("on_check", pool.on_check)):
        if command:
            problem = run_hook(command, lease, timeout_s=pool.hook_timeout_s)
            if problem:
                return f"{name} failed: {problem}"
    return None


def run_hook(command: str, lease: Lease, *, timeout_s: float) -> str | None:
    """Run a pool hook with the lease's env vars; the problem, or None when it worked."""
    return run_command(command, lease_env(lease), timeout_s=timeout_s)[0]


def run_command(
    command: str, extra_env: Mapping[str, str], *, timeout_s: float
) -> tuple[str | None, str]:
    """Run the config author's command with ``extra_env``: (the problem or None, its stdout)."""
    env = {**os.environ, **extra_env}
    try:
        done = subprocess.run(  # noqa: S602 — the config author's own command line
            command,
            shell=True,  # the config author's own command line  # nosec B602
            env=env,
            timeout=timeout_s,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout_s:g}s", ""
    except OSError as exc:
        return str(exc), ""
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or [""]
        return f"exit {done.returncode}" + (f": {tail[0][:200]}" if tail[0] else ""), done.stdout
    return None, done.stdout


def _disable_quietly(backend: LeaseBackend, lease: Lease, reason: str) -> None:
    logger.warning("taking %s/%s out of the pool: %s", lease.pool, lease.item, reason)
    try:
        backend.set_disabled(lease.pool, lease.item, disabled=True, reason=reason)
    except Exception:  # noqa: BLE001
        logger.warning("could not disable %s/%s", lease.pool, lease.item, exc_info=True)


def _with_item(pool: ResourcePool, lease: Lease) -> Lease:
    """The lease with its key's labels and data from the pool (a server may not send them)."""
    from dataclasses import replace

    item = pool.item(lease.item)
    if item is None:
        return lease
    return replace(lease, labels=item.labels, data=item.data)


# -- team server client -------------------------------------------------------------


class RemoteLeases:
    """The same calls as :class:`LeaseStore`, on a QASight server (``/api/v1/resources``)."""

    def __init__(self, server: str, token: str | None) -> None:
        self.base = f"{server.rstrip('/')}/api/v1/resources"
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _call(
        self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200,)
    ) -> tuple[int, Any]:
        import requests

        url = f"{self.base}{path}"
        try:
            resp = requests.request(
                method, url, json=body, headers=self.headers, timeout=_HTTP_TIMEOUT_S
            )
        except requests.RequestException as exc:
            raise LeaseError(f"could not reach {url}: {exc}") from exc
        if resp.status_code not in ok:
            raise LeaseError(f"{url} answered {resp.status_code}: {_detail(resp)}")
        if resp.status_code == 204 or not resp.content:
            return resp.status_code, None
        try:
            return resp.status_code, resp.json()
        except ValueError as exc:
            raise LeaseError(f"{url} answered with something that is not JSON.") from exc

    def take(
        self,
        pool: ResourcePool,
        *,
        labels: Iterable[str] = (),
        holder: Holder,
        waiter_id: str | None = None,
    ) -> TakeResult:
        _, body = self._call(
            "POST",
            "/leases",
            {
                "pool": pool.name,
                "spec": pool.to_json(hooks=False),
                "labels": list(labels),
                "holder": _holder_json(holder),
                "waiter_id": waiter_id,
            },
        )
        lease = body.get("lease")
        return TakeResult(
            lease=_lease_from_json(lease) if lease else None,
            ahead=int(body.get("ahead") or 0),
            free=int(body.get("free") or 0),
            reason=body.get("reason"),
        )

    def heartbeat(self, lease_id: str) -> Lease | None:
        status, body = self._call("POST", f"/leases/{lease_id}/heartbeat", ok=(200, 404))
        return _lease_from_json(body) if status == 200 else None

    def release(self, lease_id: str, *, clean: bool = False) -> bool:
        path = f"/leases/{lease_id}" + ("?clean=true" if clean else "")
        status, _ = self._call("DELETE", path, ok=(204, 404))
        return status == 204

    def leave(self, waiter_id: str) -> None:
        self._call("DELETE", f"/waiters/{waiter_id}", ok=(204,))

    def available(self, pool: ResourcePool, *, labels: Iterable[str] = ()) -> int:
        return count_available(self.status(pool.name), pool, labels)

    def set_disabled(
        self, pool: str, item: str, *, disabled: bool, reason: str = "", by: str | None = None
    ) -> bool:
        del by  # the server records who asked
        path = "/items/disable" if disabled else "/items/enable"
        status, _ = self._call(
            "POST", path, {"pool": pool, "item": item, "reason": reason}, ok=(204, 404)
        )
        return status == 204

    def force_release(self, pool: str, item: str) -> bool:
        status, _ = self._call(
            "POST", "/items/release", {"pool": pool, "item": item}, ok=(204, 404)
        )
        return status == 204

    def status(
        self, pool: str | None = None, *, pools: Sequence[ResourcePool] = ()
    ) -> list[PoolStatus]:
        # The server learns pools from takes; describing them is a local-database nicety.
        del pools
        _, body = self._call("GET", "" if pool is None else f"?pool={pool}")
        return [_pool_from_json(p) for p in body.get("pools", [])]

    def record_action(self, action: ProviderAction) -> None:
        body = {k: v for k, v in asdict(action).items() if k != "at"}  # the server's clock
        self._call("POST", "/actions", body, ok=(204,))

    def actions(self, pool: str | None = None, *, limit: int = 50) -> list[ProviderAction]:
        query = f"?limit={limit}" + (f"&pool={pool}" if pool else "")
        _, body = self._call("GET", f"/actions{query}")
        return [ProviderAction(**a) for a in body.get("actions", [])]


def count_available(
    status: Iterable[PoolStatus], pool: ResourcePool, labels: Iterable[str] = ()
) -> int:
    """Keys of ``pool`` with ``labels`` that nobody holds and nobody disabled."""
    busy = {i.item for s in status if s.name == pool.name for i in s.items if i.lease or i.disabled}
    return sum(1 for item in pool.matching(tuple(labels)) if item.id not in busy)


def _detail(resp: Any) -> str:
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:300] or "no details"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(body.get("detail"), str):
            return body["detail"]
    return str(body)[:300]


def _holder_json(holder: Holder) -> dict[str, Any]:
    return {
        "user": holder.user,
        "host": holder.host,
        "pid": holder.pid,
        "run_id": holder.run_id,
        "stage": holder.stage,
        "worker": holder.worker,
        "note": holder.note,
    }


def _lease_from_json(data: Mapping[str, Any]) -> Lease:
    holder = data.get("holder") or {}
    return Lease(
        lease_id=str(data.get("lease_id") or ""),
        pool=str(data["pool"]),
        item=str(data["item"]),
        holder=Holder(
            user=str(holder.get("user") or ""),
            host=str(holder.get("host") or ""),
            pid=int(holder.get("pid") or 0),
            run_id=str(holder.get("run_id") or ""),
            stage=str(holder.get("stage") or ""),
            worker=str(holder.get("worker") or ""),
            note=str(holder.get("note") or ""),
        ),
        taken_at=float(data.get("taken_at") or 0),
        heartbeat_at=float(data.get("heartbeat_at") or 0),
        expires_at=float(data.get("expires_at") or 0),
        ttl_s=float(data.get("ttl_s") or 0),
        labels=tuple(data.get("labels") or ()),
        data=tuple((str(k), str(v)) for k, v in (data.get("data") or {}).items()),
        clean=bool(data.get("clean")),
        reuse_delay_s=float(data.get("reuse_delay_s") or 0),
    )


def _pool_from_json(data: Mapping[str, Any]) -> PoolStatus:
    items = tuple(
        ItemStatus(
            pool=str(data["name"]),
            item=str(i["item"]),
            labels=tuple(i.get("labels") or ()),
            data=tuple((str(k), str(v)) for k, v in (i.get("data") or {}).items()),
            disabled=bool(i.get("disabled")),
            disabled_reason=str(i.get("disabled_reason") or ""),
            disabled_by=i.get("disabled_by"),
            lease=_lease_from_json({**i["lease"], "pool": data["name"], "item": i["item"]})
            if i.get("lease")
            else None,
            clean=bool(i.get("clean")),
        )
        for i in data.get("items", [])
    )
    return PoolStatus(name=str(data["name"]), items=items, waiting=int(data.get("waiting") or 0))


def spec_env(pool: ResourcePool) -> str:
    """``pool`` as the value of ``TESTO_RESOURCE_SPEC`` (hooks included: it stays on this machine)."""
    return json.dumps({"pool": pool.name, "spec": pool.to_json()}, sort_keys=True)


def find_pool(
    name: str, *, config_path: Path | None = None, env: Mapping[str, str] | None = None
) -> ResourcePool:
    """Pool ``name`` from ``TESTO_RESOURCE_SPEC`` (set by a stage), else from ``resources:`` in the config.

    Raises :class:`~qasight.config.errors.ConfigError` when there is no such pool.
    """
    from qasight.config.errors import ConfigValidationError
    from qasight.config.loader import discover_and_load

    source = os.environ if env is None else env
    raw = source.get(SPEC_ENV)
    if raw and config_path is None:
        try:
            sent = json.loads(raw)
        except ValueError:
            sent = None
        if (
            isinstance(sent, dict)
            and sent.get("pool") == name
            and isinstance(sent.get("spec"), dict)
        ):
            return parse_pool(name, sent["spec"])
    pools = discover_and_load(config_path=config_path).resources
    if name not in pools:
        known = ", ".join(sorted(pools)) or "none"
        raise ConfigValidationError(
            f"no resource pool {name!r} in the config's 'resources:' (pools: {known})."
        )
    return pools[name]
