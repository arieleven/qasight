"""A stage's ``resources:``: lease keys for it, or hand its workers what they need to lease their own.

Called by :mod:`qasight.engine.executor` before the stage's process starts:

- ``per: stage``: QASight waits for one key, the process gets ``TESTO_RESOURCE_ID``
  and friends, and the key goes back when the process ends.
- ``per: worker`` / ``per: test``: each test process leases its own key. The stage
  passes the pool down in ``TESTO_RESOURCE_SPEC`` (the pytest plugin acts on it; any
  other runner can call ``qasight lease run "$TESTO_RESOURCE_POOL" -- ...``), plus where
  the leases live (this machine's database, or ``TESTO_SERVER_URL``).
- ``workers: auto``: the stage starts as many workers as the pool has free keys.

See :mod:`qasight.resources` and docs/Processes & Guides/Shared Resource Pools.md.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from qasight.config.schema import Stage
from qasight.resources.client import (
    LEASE_DB_ENV,
    SERVER_URL_ENV,
    SPEC_ENV,
    TOKEN_ENV,
    HeldLease,
    acquire,
    backend_from_env,
    default_holder,
    local_lease_db_url,
)
from qasight.resources.pools import ResourcePool, StageResources


@dataclass
class PreparedResources:
    """What the stage runs with: maybe fewer workers, extra env, maybe a key it holds."""

    stage: Stage
    env: dict[str, str] = field(default_factory=dict)
    held: HeldLease | None = None


def stage_spec(resources: StageResources, *, stage: str) -> str:
    """``TESTO_RESOURCE_SPEC`` for a stage's workers (the pool with its hooks: it stays on this machine)."""
    return json.dumps(
        {
            "pool": resources.pool.name,
            "spec": resources.pool.to_json(),
            "labels": list(resources.labels),
            "per": resources.per,
            "wait": resources.wait_s,
            "stage": stage,
        },
        sort_keys=True,
    )


def _refuse_server_side_hooks(pool: ResourcePool, *, stage: str) -> None:
    """``per: stage`` hooks run in this process. With ``TESTO_RUNNER=container`` that
    process is the API server, which must never run a stage's commands: refuse.

    ``per: test`` keeps working, because its workers run the hooks inside the stage's
    own container.
    """
    from qasight.engine.backends import runner_kind
    from qasight.resources.store import LeaseError

    hooks = [name for name in ("on_take", "on_check", "on_release") if getattr(pool, name)]
    if hooks and runner_kind() == "container":
        raise LeaseError(
            f"stage {stage!r}: pool {pool.name!r} has {', '.join(hooks)} and `per: stage`, "
            "whose hooks would run on the server itself, outside the stage's container "
            "(TESTO_RUNNER=container). Use `per: test`, whose hooks run inside the "
            "container, or remove the hooks."
        )


def prepare_stage_resources(
    stage: Stage,
    *,
    plan_name: str,
    env: Mapping[str, str],
    log: Callable[[str], None],
) -> PreparedResources:
    """Lease or pass down the stage's keys. Raises ``LeaseError`` when ``per: stage`` gets none."""
    resources = stage.resources
    if resources is None:
        return PreparedResources(stage=stage)
    backend = backend_from_env(env=env)
    pool = resources.pool
    extra: dict[str, str] = {}
    # Where the leases live, for the workers: the same server, or this machine's
    # database as an absolute URL (the stage runs in another folder).
    if env.get(SERVER_URL_ENV):
        extra[SERVER_URL_ENV] = env[SERVER_URL_ENV]
        if env.get(TOKEN_ENV):
            extra[TOKEN_ENV] = env[TOKEN_ENV]
    else:
        extra[LEASE_DB_ENV] = env.get(LEASE_DB_ENV) or local_lease_db_url()

    if resources.per == "stage":
        _refuse_server_side_hooks(pool, stage=stage.name)
        last: list[tuple[int, int]] = []

        def on_wait(result: object) -> None:
            state = (getattr(result, "ahead", 0), getattr(result, "free", 0))
            if not last or last[-1] != state:
                last.append(state)
                log(f"waiting for a key of pool {pool.name!r} ({state[0]} ahead, {state[1]} free)")

        held = acquire(
            backend,
            pool,
            labels=resources.labels,
            holder=default_holder(stage=f"{plan_name}/{stage.name}", worker="stage"),
            wait_s=resources.wait_s,
            on_wait=on_wait,
        )
        log(f"holding {pool.name}/{held.id} for this stage")
        return PreparedResources(stage=stage, env={**extra, **held.env()}, held=held)

    extra[SPEC_ENV] = stage_spec(resources, stage=f"{plan_name}/{stage.name}")
    extra["TESTO_RESOURCE_POOL"] = pool.name
    if stage.workers_auto:
        free = backend.available(pool, labels=resources.labels)
        workers = max(1, free)
        log(f"workers: auto -> {workers} ({free} free key(s) in pool {pool.name!r})")
        stage = dataclasses.replace(stage, workers=workers)
    log(
        f"each {'test' if resources.per == 'test' else 'worker'} leases its own key from {pool.name!r}"
    )
    return PreparedResources(stage=stage, env=extra)
