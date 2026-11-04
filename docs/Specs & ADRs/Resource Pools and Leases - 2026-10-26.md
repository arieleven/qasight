---
type: spec
status: current
created: 2026-10-26
updated: 2026-11-04
related: "Shared Resource Pools, Team Server, Scale Runs and Stages"
---

# Resource Pools and Leases - 2026-10-26

[Index](../Index.md) · [Shared Resource Pools](../Processes%20&%20Guides/Shared%20Resource%20Pools.md) · [Scale Runs and Stages - 2026-10-24](Scale%20Runs%20and%20Stages%20-%202026-10-24.md)

## Context

QASight could run stages in parallel (`max_parallel_stages`) and pass `workers` to the framework, but nothing stopped two parallel workers from using the same scarce thing. The case that started this: flow tests on VMware VMs, where every test worker needs its own clean, identifiable VM, and several developers run the whole suite at the same time from their own laptops. The team's answer was a lock-file system tied to the process that handed out unique keys. Lock files break in known ways: a crashed process leaves its lock behind; they only work on one machine; whoever polls at the right moment wins; nobody can see who holds what; and a broken VM keeps being handed out.

The owner asked for a general answer, not a VM feature: the system under test already has its own VM actions, so QASight should only make parallel runs safe.

## Decision

**Pools of opaque keys, handed out as leases from the database.**

1. **Pools in config.** A top-level `resources:` mapping names pools of keys (`items`, or `count: N`), with optional labels and data per key, `lease_ttl`, `max_per_user`, and optional `on_take` / `on_release` shell hooks (`qasight/resources/pools.py`). QASight never interprets a key.
2. **A lease is a row.** `resource_lease` has the key (pool, item) as its primary key, so taking a key is one insert that fails for a second holder on SQLite, Postgres and MySQL alike, across processes and machines. The holder heartbeats every `lease_ttl / 3`; a row past `expires_at` is deleted by the next take, and a heartbeat after expiry is refused, so a holder that was presumed dead can't come back and share the key (`qasight/resources/store.py`).
3. **A fair queue.** A request that finds nothing free becomes a row in `resource_waiter` (with the keys it would accept) and asks again every two seconds with the same id. A key goes to the oldest waiter that can use it; a younger one goes first only when the older ones want other keys. A waiter that stops asking expires after 30 seconds.
4. **Broken keys leave the pool.** `resource_item` remembers each key as clients describe it, plus a `disabled` flag. A failing hook disables its key and the worker tries another; people disable and enable keys from the CLI or the Resources page.
5. **One client, two places.** `qasight/resources/client.py` talks to the local run-history database, or to a team server's `/api/v1/resources` when `TESTO_SERVER_URL` is set. `acquire()` waits, runs `on_take`, and returns a `HeldLease` that heartbeats in a thread and runs `on_release` and gives the key back on exit. `qasight lease run POOL -- CMD` is the CLI wrapper; workers read `TESTO_RESOURCE_ID` and friends.
6. **The server learns pools from its users.** A take request carries the pool's keys, labels and data (never its hooks), so a team server needs no copy of each repository's config, and every client decides what it would accept. Reads never show lease ids; taking a key back from someone else needs `admin` with accounts.
7. **Stages ask for keys** (phase 2). A stage's `resources:` names a pool and `per: worker | test | stage` (`qasight/engine/stage_resources.py`). `per: stage` is leased by the executor around the stage's process. For `worker` and `test` the executor only passes the pool down in `TESTO_RESOURCE_SPEC` (with where the leases live), and the pytest plugin's autouse fixtures in each worker take the keys, so the xdist controller never holds one and a key is never shared between workers. `workers: auto` sets the worker count to the pool's free keys when the stage starts. A `per: stage` stage that gets no key returns `124`, the same as a timeout.
8. **Shards by time** (phase 3). `qasight/services/test_split.py` splits a list of tests into N shards with the longest-processing-time rule (longest first, each to the least-loaded shard; within 4/3 of the best split), using each test's median duration from `list_cycle_tests` or the team server's `GET /api/v1/analytics/tests`. Ties go by name, so the split depends only on the set of tests and their times, never their order; shards on different machines agree as long as they read the same history. `pytest --qasight-shard I/N` deselects the other shards' tests (after `-k`/`-m`); under xdist the controller loads the times once and passes them to its workers in `workerinput`, so every worker picks the same tests. `qasight split` does the same for any runner. `qasight run` now sets `TESTO_CYCLE` and `TESTO_STAGE` for each stage, so the plugin finds the cycle's times.

## Options considered

- **Keep lock files, but use OS locks (`flock`).** Freed when the process dies, but still one machine, no queue, no view. Rejected for the many-laptops case.
- **Redis, etcd or Consul leases.** They have TTL leases built in, but they are another service to run and secure, and they know nothing about pools, labels or the team's runs. The database is already there on every install and on the team server.
- **Jenkins Lockable Resources / GitLab `resource_group`.** Close in spirit, but tied to one CI system and to whole jobs, not to the workers inside a job, and invisible to a developer running from a laptop.
- **Pools defined only on the server.** One source of truth, but every repository change would need a server change, and a laptop without a server (one machine, parallel workers) would need a second code path. Rejected; the server instead remembers what clients describe.
- **Reset VMs ourselves (snapshot revert).** Rejected at the owner's request: the system under test owns its actions. The hooks are the seam.

## Consequences

- Keys are safe from double use as long as every holder heartbeats; a process frozen for longer than `lease_ttl` (a debugger, a suspended VM) loses its key. Pick `lease_ttl` with that in mind; `qasight lease run` warns when it lost a lease while the command ran.
- Waiting is polling (one small request every two seconds per waiter). Fine for tens of workers; a pool with hundreds of waiters would want a push channel.
- The user name in a lease comes from the client's OS account unless the server has accounts and a signed-in session; the shared API token names no one.
- Keys nobody has described for a week, and that are free and enabled, drop off the Resources page; their rows stay.
- `workers: auto` counts free keys once; if other people take keys meanwhile, the extra workers wait in line rather than share.
- A shard that can't load the times (server down) stops with a usage error instead of splitting without them, because other shards might have them and pick differently.
- `qasight lease demo` and the demo site (`export_static_site.py --demo-resources`) fill the pools with invented data from `qasight/resources/demo.py`, so the Resources page can be tried without real keys.
- The queue position is lost if a waiter's process restarts (it gets a new id). That's the price of not keeping state on the client.

## Next

Phases 2 (stage `resources:`, `workers: auto`, the pytest options and `testo_resource` fixture) and 3 (shards by time) are done. Later, not started: per-stage CPU and memory limits in the container runner. Key checks, clean releases and the reuse delay: [Resource Lifecycle Hooks - 2026-10-26](Resource%20Lifecycle%20Hooks%20-%202026-10-26.md).
