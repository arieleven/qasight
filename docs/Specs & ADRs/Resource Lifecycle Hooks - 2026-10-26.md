---
type: spec
status: current
created: 2026-10-26
updated: 2026-11-04
related: "Resource Pools and Leases, Shared Resource Pools"
---

# Resource Lifecycle Hooks - 2026-10-26

[Index](../Index.md) · [Shared Resource Pools](../Processes%20&%20Guides/Shared%20Resource%20Pools.md) · [Resource Pools and Leases - 2026-10-26](Resource%20Pools%20and%20Leases%20-%202026-10-26.md)

## Context

[Resource Pools and Leases](Resource%20Pools%20and%20Leases%20-%202026-10-26.md) decides who holds which key, and frees a key when its holder dies. What a key *is* (a VM, an account, a database) stays with the user's own code, through the `on_take` and `on_release` hooks.

The owner's earlier design for the same problem was one class per resource: an idempotent create with a unique name, an idempotent delete, and a reset to a snapshot. We compared it with pytest fixtures, Testcontainers and its reaper, object pools (Apache Commons Pool), lease managers (Jenkins Lockable Resources, Consul, etcd) and resource providers (Terraform, Kubernetes CSI and operators). They agree on idempotent create, reset and delete. Where they go further:

- **Allocation and lifecycle are separate.** A pool or lease decides who gets what; a factory or provider knows how to create, check, reset and destroy. Leases already do the first half.
- **Check before handing out.** Object pools call `validateObject`; a reset can succeed and still leave a broken key.
- **Don't trust the release to run.** A crashed holder never resets its key, so the next taker must know.
- **A presumed-dead holder may only be frozen.** Consul holds an expired lock back for a short "lock-delay" so the old holder can notice before anyone else acts.

## Decision

Three small additions, all optional and all on the client (the team server never runs hooks):

1. **`on_check`**: a pool hook run after `on_take`. Exit `0` means the key is usable; anything else disables the key with the reason and tries another, as a failing `on_take` already did (`_prepare` in `qasight/resources/client.py`).
2. **Clean release**: `resource_item.clean` is set when a lease is given back with `clean=True` (a successful `on_release`, or `qasight lease release --clean`; `DELETE /api/v1/resources/leases/{id}?clean=true`) and cleared when the key is taken, in the same transactions as the release and the take. The taker sees it as `Lease.clean` and `TESTO_RESOURCE_CLEAN=1|0`, so `on_take` can skip a slow reset when the last holder already did one, and still resets after a crash.
3. **`reuse_delay`**: an expired lease keeps its key out of the pool for `reuse_delay` seconds more (default `lease_ttl / 3`, one heartbeat interval; stored per lease as `resource_lease.reuse_delay_s`). Heartbeats after the expiry are still refused, so a holder that wakes up finds out it lost the key before anyone else has it. `qasight lease list` and the Resources page show such a key as expired, with when it is free again.

`LeaseStore` adds the two new columns to a database that has the tables without them (`_add_missing_columns`), since these tables have no migrations.

## Options considered

- **A Python provider interface** (`provider: module:Class` with `create`, `reset`, `check`, `destroy`, `list`). The verbs map onto the hooks; worth adding when someone wants to write hooks in Python. Not needed for the shell-hook users today.
- **Dynamic pools** (keys created per run, destroyed after, a `qasight lease sweep` that destroys keys with no live lease). This is the owner's original create-and-delete design with crash cleanup. Left until there is real demand: a fixed pool reset on take covers VMs, devices, accounts and databases, and creating per run brings quotas, cost and orphan handling.
- **A reconcile loop** that keeps a desired number of keys alive. That is a controller to run and watch, and would push QASight toward managing infrastructure. Rejected.
- **Stop the command when its lease is lost** (fencing). `qasight lease run` warns today. A follow-up could stop the command instead; it changes what users see, so it waits for a decision.

## Consequences

- A key is clean only when its last holder said so. New keys, expired leases, keys taken back and pools with no `on_release` all give `TESTO_RESOURCE_CLEAN=0`, so an `on_take` that resets on `0` never hands over a dirty key.
- An expired key now comes free `reuse_delay` later than before (40 s with the default 120 s TTL). `reuse_delay: 0` restores the old timing.
- Hooks must be safe to run again on a key in any state; the guide says so and shows the pattern.

## Sources

[pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html) · [Testcontainers' Ryuk](https://dotnet.testcontainers.org/api/resource_reaper/) · [Commons Pool `PooledObjectFactory`](https://commons.apache.org/proper/commons-pool/apidocs/org/apache/commons/pool2/PooledObjectFactory.html) · [Jenkins Lockable Resources](https://plugins.jenkins.io/lockable-resources/) · [Consul sessions and lock-delay](https://developer.hashicorp.com/consul/docs/dynamic-app-config/sessions) · [Terraform: Delete](https://developer.hashicorp.com/terraform/plugin/framework/resources/delete) · [CSI idempotency](https://github.com/container-storage-interface/spec/pull/160) · [Kubebuilder finalizers](https://book.kubebuilder.io/reference/using-finalizers)
