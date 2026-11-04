"""Resource pools: the ``resources:`` section of ``qasight.yaml``.

A pool is a list of keys that parallel test workers share: VMs, phones,
test accounts, license seats. QASight never looks inside a key; it only
makes sure two workers never hold the same one at the same time (see
:mod:`qasight.resources.store`). Example::

    resources:
      flow-vms:
        items:
          - vm-01
          - { id: vm-02, labels: [win11], data: { address: 10.0.0.12 } }
        lease_ttl: 120          # seconds without a heartbeat before a key is free again
        max_per_user: 2         # optional
        on_take: "my-tool revert $TESTO_RESOURCE_ID"     # optional, your own command
        on_check: "my-tool ping $TESTO_RESOURCE_ID"      # optional: exit 0 = usable
        provider:                                         # optional, your own commands
          list: "my-tool list-vms"                        # prints the keys that exist, one per line
      seats:
        count: 3                # keys seat-1, seat-2, seat-3

The same parser reads a pool back from JSON (:meth:`ResourcePool.to_json`),
which is how a pool travels to a team server or to a stage's workers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from qasight.config.errors import ConfigValidationError

DEFAULT_LEASE_TTL_S: Final[float] = 120.0
DEFAULT_HOOK_TIMEOUT_S: Final[float] = 300.0
MAX_ITEMS: Final[int] = 1000

# Pool names and keys show up in URLs, env vars and table rows; keep them plain.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:-]{0,127}$")
_POOL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "items",
        "count",
        "lease_ttl",
        "max_per_user",
        "reuse_delay",
        "on_take",
        "on_check",
        "on_release",
        "hook_timeout",
        "provider",
    }
)
_PROVIDER_KEYS: Final[frozenset[str]] = frozenset(
    {"list", "create", "destroy", "allow", "max_items", "name_prefix"}
)
PROVIDER_VERBS: Final[tuple[str, ...]] = ("create", "destroy")
_ITEM_KEYS: Final[frozenset[str]] = frozenset({"id", "labels", "data"})


@dataclass(frozen=True)
class ResourceItem:
    """One key in a pool. ``data`` is passed to the worker untouched (as strings)."""

    id: str
    labels: tuple[str, ...] = ()
    data: tuple[tuple[str, str], ...] = ()

    def has_labels(self, wanted: Iterable[str]) -> bool:
        return set(wanted) <= set(self.labels)


@dataclass(frozen=True)
class ResourceProvider:
    """A pool's ``provider:``: the user's own commands that look at the real keys.

    Only ``qasight pool`` runs them, when a person types it; leases, runs, stages
    and the team server never do (see :mod:`qasight.resources.provider`).
    """

    # Prints the keys that exist right now, one per line. Read-only by contract.
    list: str | None = None
    # Make or remove the key in $TESTO_RESOURCE_ID. Run only by `qasight pool add`,
    # `remove` and `sweep` with --apply, and only for verbs listed in ``allow``.
    create: str | None = None
    destroy: str | None = None
    allow: tuple[str, ...] = ()
    # Hard cap on keys that exist (the pool's items plus what ``list`` shows).
    max_items: int | None = None
    # Keys made or removed through the provider must start with this.
    name_prefix: str | None = None

    def allows(self, verb: str) -> bool:
        return verb in self.allow


@dataclass(frozen=True)
class ResourcePool:
    name: str
    items: tuple[ResourceItem, ...]
    lease_ttl_s: float = DEFAULT_LEASE_TTL_S
    max_per_user: int | None = None
    # Seconds a key whose lease expired stays out of the pool, so a holder that was
    # only frozen (not dead) has a heartbeat's time to notice it lost the key.
    # None: one heartbeat interval (lease_ttl / 3).
    reuse_delay_s: float | None = None
    # Commands run (with the lease's env vars) by the process that took the key.
    on_take: str | None = None
    on_check: str | None = None
    on_release: str | None = None
    hook_timeout_s: float = DEFAULT_HOOK_TIMEOUT_S
    provider: ResourceProvider | None = None

    @property
    def reuse_delay(self) -> float:
        return self.lease_ttl_s / 3 if self.reuse_delay_s is None else self.reuse_delay_s

    def matching(self, labels: Iterable[str] = ()) -> tuple[ResourceItem, ...]:
        wanted = tuple(labels)
        return tuple(item for item in self.items if item.has_labels(wanted))

    def item(self, item_id: str) -> ResourceItem | None:
        return next((item for item in self.items if item.id == item_id), None)

    def to_json(self, *, hooks: bool = True) -> dict[str, Any]:
        """The pool as JSON. ``hooks=False`` leaves out the commands (a server never runs them).

        ``provider`` is never included: it only runs from ``qasight pool`` on the config
        author's machine, so it doesn't travel to a server or to a stage's workers.
        """
        data: dict[str, Any] = {
            "items": [
                {"id": item.id, "labels": list(item.labels), "data": dict(item.data)}
                for item in self.items
            ],
            "lease_ttl": self.lease_ttl_s,
            "max_per_user": self.max_per_user,
            "reuse_delay": self.reuse_delay_s,
        }
        if hooks:
            data.update(
                on_take=self.on_take,
                on_check=self.on_check,
                on_release=self.on_release,
                hook_timeout=self.hook_timeout_s,
            )
        return data


STAGE_PER: Final[tuple[str, ...]] = ("worker", "test", "stage")
DEFAULT_STAGE_WAIT_S: Final[float] = 1800.0
_STAGE_KEYS: Final[frozenset[str]] = frozenset({"pool", "per", "labels", "wait"})
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$")
_UNITS: Final[dict[str, float]] = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}


@dataclass(frozen=True)
class StageResources:
    """A stage's ``resources:``: which pool its workers lease from, and how.

    ``per``: ``worker`` (each test process holds one key for its whole session),
    ``test`` (pytest: a key per test, so ``on_take`` runs before every test) or
    ``stage`` (QASight holds one key while the stage runs).
    """

    pool: ResourcePool
    per: str = "worker"
    labels: tuple[str, ...] = ()
    wait_s: float | None = DEFAULT_STAGE_WAIT_S


def parse_duration(raw: Any) -> float | None:
    """``90``, ``"90s"``, ``"10m"``, ``"2h"`` in seconds; ``"forever"`` or None is no limit."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"{raw!r} is not a duration like 90s, 10m or 2h.")
    if isinstance(raw, (int, float)):
        if raw < 0:
            raise ValueError("a duration can't be negative.")
        return float(raw)
    text = str(raw).strip().lower()
    if text in ("forever", "inf", "none"):
        return None
    match = _DURATION_RE.match(text)
    if not match:
        raise ValueError(f"{raw!r} is not a duration like 90s, 10m or 2h.")
    return float(match.group(1)) * _UNITS[match.group(2)]


def parse_stage_resources(
    raw: Any, *, pools: Mapping[str, ResourcePool], where: str
) -> StageResources | None:
    """A stage's ``resources:``: a pool name, or ``{pool, per?, labels?, wait?}``."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = {"pool": raw}
    if not isinstance(raw, Mapping):
        raise ConfigValidationError(f"{where}: 'resources' must be a pool name or a mapping.")
    unknown = sorted(str(k) for k in raw if k not in _STAGE_KEYS)
    if unknown:
        raise ConfigValidationError(
            f"{where}: 'resources' has unknown key(s) {unknown}; allowed: {sorted(_STAGE_KEYS)}"
        )
    name = str(raw.get("pool") or "").strip()
    if name not in pools:
        known = ", ".join(sorted(pools)) or "none"
        raise ConfigValidationError(
            f"{where}: resources.pool {name!r} is not a pool under the top-level "
            f"'resources:' (pools: {known})."
        )
    per = str(raw.get("per") or "worker").strip()
    if per not in STAGE_PER:
        raise ConfigValidationError(
            f"{where}: resources.per must be one of {', '.join(STAGE_PER)} (got {per!r})."
        )
    labels = parse_labels(raw.get("labels"), where=f"{where}: resources.labels")
    pool = pools[name]
    if not pool.matching(labels):
        raise ConfigValidationError(
            f"{where}: no key of pool {name!r} has the labels {list(labels)}."
        )
    try:
        wait_s = parse_duration(raw["wait"]) if "wait" in raw else DEFAULT_STAGE_WAIT_S
    except ValueError as exc:
        raise ConfigValidationError(f"{where}: resources.wait: {exc}") from exc
    return StageResources(pool=pool, per=per, labels=labels, wait_s=wait_s)


def parse_resources(raw: Any) -> dict[str, ResourcePool]:
    """The top-level ``resources:`` mapping (missing means no pools)."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigValidationError("'resources:' must be a mapping of pool names to pools.")
    return {str(name): parse_pool(str(name), body) for name, body in raw.items()}


def parse_pool(name: str, raw: Any) -> ResourcePool:
    where = f"resources.{name}"
    check_name(name, kind="resource pool name")
    if not isinstance(raw, Mapping):
        raise ConfigValidationError(f"{where} must be a mapping with 'items' or 'count'.")
    unknown = sorted(str(k) for k in raw if k not in _POOL_KEYS)
    if unknown:
        raise ConfigValidationError(
            f"{where} has unknown key(s) {unknown}; allowed: {sorted(_POOL_KEYS)}"
        )
    has_items, has_count = raw.get("items") is not None, raw.get("count") is not None
    if has_items == has_count:
        raise ConfigValidationError(f"{where} needs exactly one of 'items' or 'count'.")
    if has_count:
        count = _whole_number(raw["count"], f"{where}.count", minimum=1)
        if count > MAX_ITEMS:
            raise ConfigValidationError(f"{where}.count must be at most {MAX_ITEMS}.")
        items = tuple(ResourceItem(id=f"{name}-{i}") for i in range(1, count + 1))
    else:
        items = _parse_items(raw["items"], where=where)

    max_per_user_raw = raw.get("max_per_user")
    reuse_delay_raw = raw.get("reuse_delay")
    return ResourcePool(
        name=name,
        items=items,
        lease_ttl_s=_seconds(raw.get("lease_ttl"), f"{where}.lease_ttl", DEFAULT_LEASE_TTL_S),
        max_per_user=(
            None
            if max_per_user_raw is None
            else _whole_number(max_per_user_raw, f"{where}.max_per_user", minimum=1)
        ),
        reuse_delay_s=(
            None
            if reuse_delay_raw is None
            else _seconds(reuse_delay_raw, f"{where}.reuse_delay", 0.0, allow_zero=True)
        ),
        on_take=_command(raw.get("on_take"), f"{where}.on_take"),
        on_check=_command(raw.get("on_check"), f"{where}.on_check"),
        on_release=_command(raw.get("on_release"), f"{where}.on_release"),
        hook_timeout_s=_seconds(
            raw.get("hook_timeout"), f"{where}.hook_timeout", DEFAULT_HOOK_TIMEOUT_S
        ),
        provider=_parse_provider(raw.get("provider"), where=f"{where}.provider"),
    )


def _parse_provider(raw: Any, *, where: str) -> ResourceProvider | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConfigValidationError(f"{where} must be a mapping of verbs to your commands.")
    unknown = sorted(str(k) for k in raw if k not in _PROVIDER_KEYS)
    if unknown:
        raise ConfigValidationError(
            f"{where} has unknown key(s) {unknown}; allowed: {sorted(_PROVIDER_KEYS)}"
        )
    allow_raw = raw.get("allow")
    if allow_raw is None:
        allow: tuple[str, ...] = ()
    elif isinstance(allow_raw, list) and all(v in PROVIDER_VERBS for v in allow_raw):
        allow = tuple(dict.fromkeys(allow_raw))
    else:
        raise ConfigValidationError(
            f"{where}.allow must be a list of {', '.join(PROVIDER_VERBS)} (got {allow_raw!r})."
        )
    max_items_raw = raw.get("max_items")
    prefix_raw = raw.get("name_prefix")
    provider = ResourceProvider(
        list=_command(raw.get("list"), f"{where}.list"),
        create=_command(raw.get("create"), f"{where}.create"),
        destroy=_command(raw.get("destroy"), f"{where}.destroy"),
        allow=allow,
        max_items=(
            None
            if max_items_raw is None
            else _whole_number(max_items_raw, f"{where}.max_items", minimum=1)
        ),
        name_prefix=None if prefix_raw is None else str(prefix_raw).strip(),
    )
    if provider.name_prefix is not None:
        check_name(provider.name_prefix, kind=f"{where}.name_prefix")
    # Each allowed verb needs its command and the limits that keep it safe.
    for verb in allow:
        needs = {
            verb: getattr(provider, verb),
            "list": provider.list,
            "name_prefix": provider.name_prefix,
        }
        if verb == "create":
            needs["max_items"] = provider.max_items
        missing = [key for key, value in needs.items() if value is None]
        if missing:
            raise ConfigValidationError(
                f"{where}.allow has {verb!r}, which also needs {', '.join(missing)}."
            )
    return provider


def check_name(value: str, *, kind: str) -> None:
    if not _NAME_RE.match(value):
        raise ConfigValidationError(
            f"{kind} {value!r} must be 1-128 letters, digits or . _ @ : - "
            "and start with a letter or digit."
        )


def _parse_items(raw: Any, *, where: str) -> tuple[ResourceItem, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigValidationError(f"{where}.items must be a non-empty list.")
    if len(raw) > MAX_ITEMS:
        raise ConfigValidationError(f"{where}.items can hold at most {MAX_ITEMS} keys.")
    items: list[ResourceItem] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        label = f"{where}.items[{i}]"
        if isinstance(entry, (str, int)) and not isinstance(entry, bool):
            entry = {"id": str(entry)}
        if not isinstance(entry, Mapping):
            raise ConfigValidationError(f"{label} must be a key name or a mapping with 'id'.")
        unknown = sorted(str(k) for k in entry if k not in _ITEM_KEYS)
        if unknown:
            raise ConfigValidationError(
                f"{label} has unknown key(s) {unknown}; allowed: {sorted(_ITEM_KEYS)}"
            )
        item_id = str(entry.get("id") or "").strip()
        check_name(item_id, kind=f"{label}.id")
        if item_id in seen:
            raise ConfigValidationError(f"{label}: key {item_id!r} is listed twice.")
        seen.add(item_id)
        items.append(
            ResourceItem(
                id=item_id,
                labels=parse_labels(entry.get("labels"), where=f"{label}.labels"),
                data=_parse_data(entry.get("data"), where=f"{label}.data"),
            )
        )
    return tuple(items)


def parse_labels(raw: Any, *, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ConfigValidationError(f"{where} must be a list of non-empty strings.")
    return tuple(dict.fromkeys(x.strip() for x in raw))


def _parse_data(raw: Any, *, where: str) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise ConfigValidationError(f"{where} must be a mapping of names to values.")
    out: list[tuple[str, str]] = []
    for key, value in raw.items():
        if isinstance(value, (Mapping, list)):
            raise ConfigValidationError(f"{where}.{key} must be a plain value, not a list or map.")
        out.append((str(key), "" if value is None else str(value)))
    return tuple(out)


def _whole_number(raw: Any, where: str, *, minimum: int) -> int:
    # bool is an int subclass; `count: true` is a typo, not 1.
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        raise ConfigValidationError(f"{where} must be a whole number of at least {minimum}.")
    return raw


def _seconds(raw: Any, where: str, default: float, *, allow_zero: bool = False) -> float:
    if raw is None:
        return default
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or raw < 0
        or (raw == 0 and not allow_zero)
    ):
        floor = "0 or more" if allow_zero else "above 0"
        raise ConfigValidationError(f"{where} must be a number of seconds {floor}.")
    return float(raw)


def _command(raw: Any, where: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigValidationError(f"{where} must be a non-empty command string.")
    return raw.strip()
