---
type: guide
status: current
created: 2026-10-26
updated: 2026-11-04
---

# Shared Resource Pools

[Index](../Index.md) · [Command Reference § qasight lease](../CLI%20Commands/Command%20Reference.md#qasight-lease) · [Resource Pools and Leases - 2026-10-26](../Specs%20&%20ADRs/Resource%20Pools%20and%20Leases%20-%202026-10-26.md)

Parallel test workers often need something that only one of them may use at a time: a VM, a phone, a test account, a license seat, a port. A resource pool is a list of those things, by name. QASight gives each worker its own key from the pool and makes sure no other worker, on this machine or on another developer's laptop, gets the same key until it is given back.

QASight never does anything to the keys themselves. Reverting a VM, logging in to an account or rebooting a phone stays with your own tools; you can call them from optional hooks.

## Define a pool

In `qasight.yaml` (a file with only `resources:` and no `cycles:` works too):

```yaml
resources:
  flow-vms:
    items:
      - vm-01
      - vm-02
      - { id: vm-03, labels: [win11], data: { address: 10.0.0.13 } }
    lease_ttl: 120                                   # seconds without a heartbeat before a key is free again
    max_per_user: 4                                  # optional: most keys one person may hold
    reuse_delay: 40                                  # optional: seconds an expired key stays out
    on_take: "vmrun revertToSnapshot $TESTO_RESOURCE_ID clean"   # optional, your own command
    on_check: "./vm-ready.sh $TESTO_RESOURCE_ID"     # optional: exit 0 if the key is usable
    on_release: "my-cleanup $TESTO_RESOURCE_ID"      # optional
    hook_timeout: 300                                # seconds, for each hook
    provider:                                        # optional: your own commands for `qasight pool`
      list: "./vms.sh list"                          # prints the VMs that exist, one per line
      create: "./vms.sh create $TESTO_RESOURCE_ID"   # only `qasight pool add --apply` runs it
      destroy: "./vms.sh delete $TESTO_RESOURCE_ID"  # only `qasight pool remove/sweep --apply`
      allow: [create, destroy]                       # without this, both are refused
      max_items: 6                                   # never more VMs than this
      name_prefix: "testo-flow-"                     # create and destroy only touch these names
  license-seats:
    count: 3                                         # keys license-seats-1 ... license-seats-3
```

| Key | Default | Meaning |
|-----|---------|---------|
| `items` | | Key names (letters, digits, `. _ @ : -`), or `{id, labels, data}`. `labels` let a worker ask for a kind of key; `data` is handed to the worker as is |
| `count` | | Instead of `items`: that many keys named `<pool>-1` to `<pool>-N` |
| `lease_ttl` | `120` | Seconds a lease lives without a heartbeat. A worker that dies frees its key after this long |
| `max_per_user` | none | Most keys one user may hold at once; more requests wait |
| `reuse_delay` | `lease_ttl / 3` | Seconds a key whose lease expired stays out of the pool, so a holder that was only frozen (a debugger, a sleeping laptop) finds out it lost the key before anyone else gets it. `0` hands it out at once |
| `on_take` | none | Shell command run, with the variables below set, right after a key is taken: reset it here. If it fails, the key is taken out of the pool (disabled) and another key is tried |
| `on_check` | none | Shell command run after `on_take`: exit `0` if the key is usable. If it fails, the key is disabled and another key is tried |
| `on_release` | none | Shell command run before a key is given back. If it works, the key is marked clean for the next taker (`TESTO_RESOURCE_CLEAN=1`). If it fails, the key is disabled |
| `hook_timeout` | `300` | Seconds each hook may take; longer counts as a failure |
| `provider.list` | none | Shell command that prints the keys that really exist, one per line (blank lines and `#` lines skipped). Only `qasight pool` runs it, when you type it ([below](#compare-the-pool-with-what-really-exists)). Never sent to a server or to a stage's workers |
| `provider.create` / `provider.destroy` | none | Shell commands that make or remove the key in `$TESTO_RESOURCE_ID`. Only `qasight pool add`, `remove` and `sweep` with `--apply` run them ([below](#add-or-remove-vms-yourself)) |
| `provider.allow` | none | Which of `create`, `destroy` may run at all. An allowed verb needs its command, `list` and `name_prefix`; `create` also needs `max_items` |
| `provider.max_items` | none | Most keys that may exist: the pool's `items` plus what `list` shows |
| `provider.name_prefix` | none | Keys made or removed through the provider must start with this, so `destroy` can't reach other VMs |

## Give each worker its own key

Wrap the command a worker runs:

```bash
qasight lease run flow-vms -- pytest tests/flows -k checkout
qasight lease run flow-vms --label win11 --wait 10m -- ./run_flow.sh
```

`qasight lease run` waits for a free key, runs the command with these variables set, keeps the lease alive while it runs, and gives the key back when it ends, whether it passed, failed or was stopped. It exits with the command's exit code.

| Variable | Example | Meaning |
|----------|---------|---------|
| `TESTO_RESOURCE_ID` | `vm-03` | The key this worker holds |
| `TESTO_RESOURCE_POOL` | `flow-vms` | Its pool |
| `TESTO_RESOURCE_LABELS` | `win11` | Its labels, comma-separated |
| `TESTO_RESOURCE_DATA` | `{"address": "10.0.0.13"}` | Its `data`, as JSON |
| `TESTO_RESOURCE_<NAME>` | `TESTO_RESOURCE_ADDRESS=10.0.0.13` | One variable per `data` value |
| `TESTO_RESOURCE_CLEAN` | `1` | `1` when the last holder gave the key back after a successful `on_release` (or `qasight lease release --clean`); `0` when it is new, expired, was taken back, or the pool has no `on_release` |
| `TESTO_LEASE_ID` | `5f0c…` | Unique per lease: a safe name for this worker's own folder or log |

Scripts that can't wrap a command use `take`, `heartbeat` and `release`; see [Command Reference § qasight lease](../CLI%20Commands/Command%20Reference.md#qasight-lease). Run several `qasight lease run` at once (one per worker, from a shell loop, a CI matrix or your own runner) and each gets a different key; when there are more workers than keys, the extra ones wait in line.

## Use a pool in a cycle

A stage can ask for keys itself with `resources:`, so `qasight run` hands them out:

```yaml
cycles:
  flows:
    stages:
      - name: flow-tests
        equipment: pytest
        args: "tests/flows"
        workers: auto                 # as many pytest-xdist workers as the pool has free keys
        resources:
          pool: flow-vms
          per: worker                 # worker (default), test, or stage
          labels: [win11]             # optional: only keys with these labels
          wait: 30m                   # how long to wait for a key (default 30m; "forever" for no limit)
      - name: deploy-check
        equipment: command
        args: "./check_all.sh"
        resources: { pool: flow-vms, per: stage }
```

`resources: flow-vms` is short for `{pool: flow-vms}`.

| `per` | Who takes a key | When it goes back |
|-------|-----------------|-------------------|
| `worker` | Each pytest(-xdist) worker, once, for all the tests it runs | When the worker ends |
| `test` | Each test (pytest only); `on_take` and `on_release` run around every test, so each test gets a freshly reset key | After the test |
| `stage` | QASight, before the stage starts. The stage's process gets the variables above | When the stage ends. With no key within `wait`, the stage fails like a timeout (`qasight run` exits `3`) |

A `per: stage` key is taken by the process that runs the stage, so its `on_take` / `on_check` / `on_release` run there too. On a team server with `TESTO_RUNNER=container` that process is the server itself, so a `per: stage` pool with hooks is refused (the stage fails like a timeout and says why); use `per: test`, whose hooks run inside the stage's container.

With `per: worker` or `per: test`, the stage passes the pool down in `TESTO_RESOURCE_SPEC` and the pytest plugin in each worker takes the key, so the pytest-xdist controller holds none. Other runners can take one per worker with `qasight lease run "$TESTO_RESOURCE_POOL" -- ...`. `workers: auto` needs `per: worker` or `per: test`, and counts free keys once, when the stage starts; a worker that finds none free waits in line.

The same works in plain pytest, without a cycle:

```bash
pytest -n 4 --qasight-resource flow-vms                         # a key per worker
pytest --qasight-resource flow-vms --qasight-resource-per test --qasight-resource-label win11
```

and a test reads its key from the `testo_resource` fixture (or from the variables):

```python
def test_checkout(testo_resource):
    vm = testo_resource.id  # "vm-03"
    address = testo_resource.data["address"]  # from the key's data
```

## Split tests by time

With pytest-xdist and `per: worker`, workers already share the tests as they go. For separate processes (CI matrix jobs, or one `qasight lease run` per key), split the suite up front into shards that take about the same time, from the times QASight recorded for each test:

```bash
# CI matrix job I of 4 (the same in every job: shards that see the same history agree)
TESTO_SHARD=$I/4 qasight run --cycle flows
# or plain pytest
pytest --qasight-shard 2/4 --qasight-timings flows tests/flows

# one shard per key of a pool, all at once on this machine
for i in 1 2 3 4; do
  qasight lease run flow-vms -- pytest --qasight-shard $i/4 --qasight-timings flows tests/flows &
done; wait
```

The longest tests are placed first, each in the shard with the least time so far; a test with no recorded time counts as a typical one, and with no history at all the split is round robin. Times come from this machine's history, or from the team server when `TESTO_SERVER_URL` is set, so CI jobs and laptops should read from the same place. For other runners, `qasight split` prints a shard's tests ([Command Reference § qasight split](../CLI%20Commands/Command%20Reference.md#qasight-split)).

## Many developers, many laptops

Leases are kept in a database, never in files:

- **One machine:** without a server they go into this machine's run-history database (`DATABASE_URL`, `database.url`, or `./testo_history.db`). All workers on the machine share one pool.
- **A team:** set `TESTO_SERVER_URL` to the [team server](Team%20Server.md) and `TESTO_API_TOKEN` to its token, on every laptop and CI agent. Every `qasight lease` then takes keys through the server, so two developers can never get the same key. If the server can't be reached, `qasight lease` fails (exit `3`); it never guesses a key.

Each lease records who holds it: the user name and host of the machine, the process, and with accounts on the server, the signed-in account. Every run keeps its own artifacts folder, and `TESTO_LEASE_ID` names a per-worker folder, so developers' files don't clash either.

## Write the hooks

Hooks are where your own tools create, reset and check the things in a pool; QASight only runs them, on the machine that took the key (never on the team server). The same three hooks fit any kind of key:

| Hook | Its job | A VM | A test account | A database |
|------|---------|------|----------------|------------|
| `on_take` | Reset to a known state | Revert to the `clean` snapshot | Reset the password, delete its data | Restore a template |
| `on_check` | Is it usable? | It answers on the network | It can log in | It accepts a connection |
| `on_release` | Tidy up for the next one | Revert now, so the next take is fast | Log it out | Drop test data |

Write them so they can run again on a key in any state: "already reverted" or "already gone" counts as success, because a crash can stop any hook halfway, and the next taker runs `on_take` on whatever was left. Exit `0` when it worked; anything else takes the key out of the pool with the hook's last line of output as the reason.

**Reset on take, not only on release.** A worker that crashes never runs `on_release`, so the next taker gets the key as the crashed worker left it. To reset only when needed, skip the slow part when the last holder already did it:

```bash
# on_take: revert only if the previous holder didn't leave it clean
[ "$TESTO_RESOURCE_CLEAN" = 1 ] || vmrun revertToSnapshot "$TESTO_RESOURCE_ID" clean
```

with the revert also in `on_release`, so a normal run pays for it once, at the end, and a crash still never hands over a dirty VM.

Keys are made outside test runs: create the VMs, accounts or databases once with your own scripts and list them under `items`. QASight doesn't create or delete keys.

## See and fix the pool

The **Resources** page in the UI (under Tools) shows every pool, each key's holder, when it was taken and its last heartbeat, and how many requests wait. A key whose holder stopped heartbeating shows as **Expired** with when it is free again. It refreshes every few seconds. From there, or with `qasight lease`, you can:

- **Disable** a broken key so nobody gets it (`qasight lease disable flow-vms vm-02 --reason "snapshot broken"`), and put it back (`qasight lease enable`). Keys whose hooks failed show up here, disabled, with the reason.
- **Try it without real pools:** `qasight lease demo` adds made-up pools, holders, a disabled key and a short queue to this machine's database, and `qasight lease demo --clear` gives the keys back. The [live demo](https://arieleven.github.io/qasight/) shows the same made-up pools.
- **Take a key back** from its holder (`qasight lease free`; an admin on a server with accounts). The holder's next heartbeat finds it gone, and `qasight lease run` warns that the lease was lost.

## Compare the pool with what really exists

QASight never creates or deletes a VM, or any key, on its own. To see whether the pool still matches your real VMs, give it a read-only `provider.list` command and run:

```bash
qasight pool status              # every pool: missing keys, VMs outside the pool
qasight pool status flow-vms --check   # also run on_check on each free key
```

| Column | Meaning |
|--------|---------|
| exists | `yes` when `provider.list` printed the key, `missing` when it didn't, `?` when the pool has no `provider.list`. Lines `provider.list` printed that the pool doesn't have show as `not in pool` (leftovers, or VMs to add to `items`) |
| lease | `free`, `held` or `disabled`, as in `qasight lease list` |
| check | With `--check`: `ok`, or why `on_check` failed. A key someone holds, or one `provider.list` doesn't show, is skipped, so a check never disturbs a running test |

It only reads: it never disables a key, takes a lease or changes your VMs. Fix what it finds with your own scripts and `items`. Exit `0` when every pool matches, `1` when a key is missing, not in the pool or fails its check, `3` when a `provider.list` command fails or the lease server can't be reached. A CI schedule can run it to warn about leftovers. Why it works this way: [Human-Run Resource Provider - 2026-10-30](../Specs%20&%20ADRs/Human-Run%20Resource%20Provider%20-%202026-10-30.md).

## Add or remove VMs yourself

Making or deleting a VM is always your decision, typed as a command. QASight runs your `provider.create` or `provider.destroy`, never on its own: not from a run, a lease, an expired lease, a schedule or the team server.

```bash
qasight pool add flow-vms --count 2           # dry run: prints the commands and the new names
qasight pool add flow-vms --count 2 --apply   # asks, then runs ./vms.sh create for testo-flow-1, testo-flow-2
qasight pool remove flow-vms testo-flow-2 --apply --yes
qasight pool sweep flow-vms                   # dry run: leftovers to remove
qasight pool log                              # who ran what, from where, and how it went
```

| Rail | What it does |
|------|--------------|
| Dry run | Without `--apply` nothing runs; you see each command and key. `--apply` asks first unless `--yes` |
| `allow` | A verb not listed is refused, even typed by hand. `TESTO_PROVIDER_ACTIONS=0` refuses both on a machine |
| Limits | At most 5 keys per command; `add` stops at `max_items`; names must start with `name_prefix` |
| Safe deletes | `remove` and `sweep` skip a key that is in `items`, one someone holds, and one `provider.list` doesn't show. `sweep` only takes listed keys with the prefix that aren't in the pool |
| No guessing | If `provider.list` fails, nothing runs |
| Log | Every command run with `--apply` is logged with the person, machine, time and result, in this machine's database or on the team server. `qasight pool log` and the Resources page show it |

`add` doesn't change your config: put the new keys under `items` in `qasight.yaml` to use them (until then `qasight pool status` shows them as `not in pool`). To remove a VM from the pool, take it out of `items` first, then run `remove`.

## How it works

- **A lease, not a lock.** A lease is a database row with an owner and an expiry time, one row per key. Taking a key is one insert, so two workers can't get the same key on any database, from any number of machines.
- **Heartbeats.** The holder pushes the expiry forward every `lease_ttl / 3` seconds. A process that crashes, is killed, or whose laptop goes to sleep stops sending them, and its key is free once `lease_ttl` and then `reuse_delay` pass. No stale locks to delete by hand. A heartbeat after the expiry is refused, so a holder that wakes up learns it lost the key (`qasight lease run` warns) instead of sharing it.
- **Clean or not.** Each key remembers whether its last lease ended with a successful `on_release`. Taking the key clears the mark, so an expired or taken-back lease never leaves a key marked clean.
- **Fair queue.** A request that finds no free key waits in line, oldest first. A request only goes ahead of an older one when they want different keys (other labels), so nobody waits behind someone who can't use the free key anyway.
- **One clock.** All times come from the database's owner: the team server, or this machine.

The pools are described by whoever uses them, so the server needs no copy of your config: each request carries the pool's keys, labels and data (never its hooks, which only run on the machine that took the key). Code: `qasight/resources/` (`pools.py`, `store.py`, `client.py`), `qasight_api/routes/resources.py`, tables `resource_item`, `resource_lease`, `resource_waiter` and `resource_action` (the provider log). Provider commands: `qasight/resources/provider.py`, `qasight/cli/commands/pool.py`.
