"""A pool's ``provider:``: the config author's own commands for the real keys.

QASight never creates or deletes a key on its own. Only
:mod:`qasight.cli.commands.pool` calls this module, when a person types
``qasight pool``. Leases, runs, stages and the team server never import it (a
contract test checks).

- ``list`` prints the keys that exist, one per line (blank lines and ``#`` comments
  are skipped). ``qasight pool status`` compares them with the pool, and with
  ``--check`` runs the pool's ``on_check`` on each free key.
- ``create`` and ``destroy`` run only from ``qasight pool add/remove/sweep --apply``,
  only for verbs under ``allow``, at most :data:`MAX_KEYS_PER_COMMAND` keys a
  time, within ``max_items``, and only on names starting with ``name_prefix``.
  ``destroy`` never touches a key in the pool's ``items`` or one someone holds.
  ``TESTO_PROVIDER_ACTIONS=0`` refuses both on a machine.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from qasight.config.errors import ConfigValidationError
from qasight.resources.client import run_command
from qasight.resources.pools import ResourceItem, ResourcePool, check_name
from qasight.resources.store import ItemStatus

MAX_KEYS_PER_COMMAND: Final[int] = 5
ACTIONS_ENV: Final[str] = "TESTO_PROVIDER_ACTIONS"


@dataclass(frozen=True)
class Listing:
    """What ``provider.list`` printed: the keys, or why it could not run."""

    keys: tuple[str, ...] = ()
    problem: str | None = None


@dataclass(frozen=True)
class KeyReport:
    key: str
    # True/False when provider.list ran; None when the pool has no list command.
    exists: bool | None
    state: str  # free | held | disabled
    # None: not checked; "" passed; otherwise why on_check failed or was skipped.
    check: str | None = None
    check_skipped: bool = False


@dataclass(frozen=True)
class PoolReport:
    pool: str
    keys: tuple[KeyReport, ...]
    # Keys provider.list printed that the pool doesn't have: leftovers, or new ones to add.
    extra: tuple[str, ...] = ()
    list_problem: str | None = None
    has_list: bool = False
    missing: tuple[str, ...] = field(init=False)
    failed_checks: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "missing", tuple(k.key for k in self.keys if k.exists is False))
        object.__setattr__(
            self,
            "failed_checks",
            tuple(k.key for k in self.keys if k.check and not k.check_skipped),
        )

    @property
    def healthy(self) -> bool:
        return not (self.list_problem or self.missing or self.extra or self.failed_checks)


def item_env(pool: ResourcePool, item: ResourceItem) -> dict[str, str]:
    """``TESTO_RESOURCE_*`` for a key nobody holds (no ``TESTO_LEASE_ID``)."""
    env = {
        "TESTO_RESOURCE_ID": item.id,
        "TESTO_RESOURCE_POOL": pool.name,
        "TESTO_RESOURCE_LABELS": ",".join(item.labels),
        "TESTO_RESOURCE_DATA": json.dumps(dict(item.data), sort_keys=True),
    }
    for key, value in item.data:
        name = "".join(c if c.isalnum() else "_" for c in key.upper())
        env.setdefault(f"TESTO_RESOURCE_{name}", value)
    return env


def list_keys(pool: ResourcePool) -> Listing:
    """Run ``provider.list``; an empty listing when the pool has none."""
    command = pool.provider.list if pool.provider else None
    if not command:
        return Listing()
    problem, out = run_command(
        command, {"TESTO_RESOURCE_POOL": pool.name}, timeout_s=pool.hook_timeout_s
    )
    if problem:
        return Listing(problem=f"provider.list failed: {problem}")
    keys = (line.strip() for line in out.splitlines())
    return Listing(keys=tuple(dict.fromkeys(k for k in keys if k and not k.startswith("#"))))


def check_key(pool: ResourcePool, item: ResourceItem) -> str:
    """Run ``on_check`` on a key nobody holds: "" when it passed, else why not."""
    if not pool.on_check:
        return ""
    problem, _ = run_command(pool.on_check, item_env(pool, item), timeout_s=pool.hook_timeout_s)
    return f"on_check failed: {problem}" if problem else ""


def pool_report(
    pool: ResourcePool, items: Sequence[ItemStatus] = (), *, check: bool = False
) -> PoolReport:
    """Compare ``pool`` with what its provider lists, and optionally check each free key.

    ``items`` is the pool's lease status (who holds what); keys someone holds are
    never checked, so a check can't disturb a test that is using the key.
    """
    listing = list_keys(pool)
    has_list = bool(pool.provider and pool.provider.list)
    listed = set(listing.keys)
    status: Mapping[str, ItemStatus] = {i.item: i for i in items}
    reports: list[KeyReport] = []
    for item in pool.items:
        st = status.get(item.id)
        state = "disabled" if st and st.disabled else "held" if st and st.lease else "free"
        exists = None if not has_list or listing.problem else item.id in listed
        result: str | None = None
        skipped = False
        if check and pool.on_check:
            if state == "held":
                result, skipped = "skipped: someone holds it", True
            elif exists is False:
                result, skipped = "skipped: provider.list doesn't show it", True
            else:
                result = check_key(pool, item)
        reports.append(
            KeyReport(key=item.id, exists=exists, state=state, check=result, check_skipped=skipped)
        )
    known = {item.id for item in pool.items}
    return PoolReport(
        pool=pool.name,
        keys=tuple(reports),
        extra=tuple(k for k in listing.keys if k not in known),
        list_problem=listing.problem,
        has_list=has_list,
    )


@dataclass(frozen=True)
class Step:
    """One provider command a plan would run: ``verb`` on ``key``."""

    verb: str
    key: str
    command: str


@dataclass(frozen=True)
class Plan:
    pool: str
    steps: tuple[Step, ...] = ()
    # Keys left out, and why.
    refused: tuple[tuple[str, str], ...] = ()
    note: str = ""


class ProviderRefused(ValueError):
    """The provider may not do this at all (the message says why)."""


def actions_switched_off(env: Mapping[str, str] | None = None) -> bool:
    value = (os.environ if env is None else env).get(ACTIONS_ENV, "")
    return value.strip().lower() in ("0", "false", "no", "off")


def _require(pool: ResourcePool, verb: str, listing: Listing) -> tuple[str, str]:
    """The verb's command and the name prefix, or :class:`ProviderRefused`."""
    provider = pool.provider
    if provider is None or not provider.allows(verb):
        raise ProviderRefused(
            f"pool {pool.name!r} doesn't allow {verb!r}: list it under "
            f"resources.{pool.name}.provider.allow (with its command) to use it."
        )
    if actions_switched_off():
        raise ProviderRefused(f"{ACTIONS_ENV}=0 on this machine: create and destroy are off.")
    if listing.problem:
        raise ProviderRefused(f"{listing.problem}; not guessing which keys exist.")
    command = getattr(provider, verb)
    assert command and provider.name_prefix  # parse_pool checks allowed verbs have both
    return command, provider.name_prefix


def _too_many(count: int) -> None:
    if count > MAX_KEYS_PER_COMMAND:
        raise ProviderRefused(
            f"at most {MAX_KEYS_PER_COMMAND} keys per command (asked for {count})."
        )


def plan_add(
    pool: ResourcePool, listing: Listing, *, names: Sequence[str] = (), count: int = 0
) -> Plan:
    """New keys to ``create``: the given ``names``, or ``count`` names made from the prefix."""
    command, prefix = _require(pool, "create", listing)
    if names and count:
        raise ProviderRefused("give key names or --count, not both.")
    _too_many(len(names) or count)
    existing = {item.id for item in pool.items} | set(listing.keys)
    wanted: list[str] = []
    refused: list[tuple[str, str]] = []
    for name in names:
        try:
            check_name(name, kind="key")
        except ConfigValidationError as exc:
            refused.append((name, str(exc)))
            continue
        if not name.startswith(prefix):
            refused.append((name, f"doesn't start with name_prefix {prefix!r}"))
        elif name in existing or name in wanted:
            refused.append((name, "already exists"))
        else:
            wanted.append(name)
    n = 1
    while len(wanted) < count:
        candidate = f"{prefix}{n}"
        if candidate not in existing and candidate not in wanted:
            wanted.append(candidate)
        n += 1
    room = max(0, (pool.provider.max_items or 0) - len(existing)) if pool.provider else 0
    for name in wanted[room:]:
        refused.append(
            (name, f"max_items {pool.provider.max_items if pool.provider else 0} reached")
        )
    return Plan(
        pool=pool.name,
        steps=tuple(Step("create", name, command) for name in wanted[:room]),
        refused=tuple(refused),
    )


def plan_remove(
    pool: ResourcePool, listing: Listing, items: Sequence[ItemStatus], names: Sequence[str]
) -> Plan:
    """Keys to ``destroy``, each only if it is safe to."""
    command, prefix = _require(pool, "destroy", listing)
    _too_many(len(names))
    steps: list[Step] = []
    refused: list[tuple[str, str]] = []
    for name in dict.fromkeys(names):
        why = _unsafe_to_destroy(pool, listing, items, name, prefix)
        if why:
            refused.append((name, why))
        else:
            steps.append(Step("destroy", name, command))
    return Plan(pool=pool.name, steps=tuple(steps), refused=tuple(refused))


def plan_sweep(pool: ResourcePool, listing: Listing, items: Sequence[ItemStatus]) -> Plan:
    """Leftovers to ``destroy``: listed keys with the prefix that aren't in the pool or held."""
    command, prefix = _require(pool, "destroy", listing)
    candidates = [k for k in listing.keys if k.startswith(prefix) and pool.item(k) is None]
    safe: list[str] = []
    refused: list[tuple[str, str]] = []
    for key in candidates:
        why = _unsafe_to_destroy(pool, listing, items, key, prefix)
        if why:
            refused.append((key, why))
        else:
            safe.append(key)
    more = len(safe) - MAX_KEYS_PER_COMMAND
    return Plan(
        pool=pool.name,
        steps=tuple(Step("destroy", key, command) for key in safe[:MAX_KEYS_PER_COMMAND]),
        refused=tuple(refused),
        note=f"{more} more after these; run sweep again." if more > 0 else "",
    )


def _unsafe_to_destroy(
    pool: ResourcePool,
    listing: Listing,
    items: Sequence[ItemStatus],
    key: str,
    prefix: str,
) -> str | None:
    if pool.item(key) is not None:
        return "it is in the pool's items; take it out of qasight.yaml first"
    if not key.startswith(prefix):
        return f"doesn't start with name_prefix {prefix!r}"
    held = next((i.lease for i in items if i.item == key and i.lease), None)
    if held is not None:
        return f"{held.holder.user or 'someone'} holds it"
    if key not in listing.keys:
        return "provider.list doesn't show it (already gone?)"
    return None


def run_step(pool: ResourcePool, step: Step) -> tuple[str | None, str]:
    """Run one planned command with ``TESTO_RESOURCE_ID`` set: (the problem or None, output)."""
    item = pool.item(step.key) or ResourceItem(id=step.key)
    problem, out = run_command(step.command, item_env(pool, item), timeout_s=pool.hook_timeout_s)
    return problem, _tail(out)


def _tail(text: str, limit: int = 300) -> str:
    lines = [line for line in re.split(r"\r?\n", text.strip()) if line.strip()]
    return " | ".join(lines[-3:])[-limit:]
