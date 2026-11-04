---
type: spec
status: current
created: 2026-10-30
updated: 2026-11-04
related: "Shared Resource Pools, Resource Pools and Leases, Resource Lifecycle Hooks"
---

# Human-Run Resource Provider - 2026-10-30

[Index](../Index.md) · [Shared Resource Pools](../Processes%20&%20Guides/Shared%20Resource%20Pools.md) · [Resource Lifecycle Hooks - 2026-10-26](Resource%20Lifecycle%20Hooks%20-%202026-10-26.md)

## Context

[Resource Lifecycle Hooks](Resource%20Lifecycle%20Hooks%20-%202026-10-26.md) left a "provider" (`create`, `reset`, `check`, `destroy`, `list`) and dynamic pools as options. The owner's rule for them: **QASight must never create or delete VMs on its own.** Pools stay fixed lists of keys in `qasight.yaml`; the only automatic commands are the user's own hooks, which reset or check a key that already exists.

What a provider still adds under that rule: seeing whether a pool matches the real VMs (keys that vanished, leftovers outside the pool), and later a safe, logged way for a person to add or remove VMs.

## Decision

A pool's `provider:` holds the user's own shell commands. QASight ships no VMware, cloud or hypervisor code and holds no credentials. Provider commands run only from `qasight pool`, typed by a person, on that person's machine.

1. **Read-only status** (pull request 206). `provider.list` prints the keys that exist. `qasight pool status [POOL] [--check]` compares that with the pool, shows each key's lease state, and with `--check` runs the pool's existing `on_check` on free keys (a held key is never checked). It changes nothing: no disable, no lease, no command besides `list` and `on_check`. Exit `1` when a pool needs a look, so a CI schedule can warn.
2. **Never automatic, enforced.** `ResourcePool.to_json` never includes `provider`, so it never reaches a team server or a stage's workers. `tests/unit/qasight/test_pool_provider.py` fails if any module besides `qasight/cli/commands/pool.py` imports `qasight.resources.provider`.
3. **`on_check` is reused**, not a second `provider.check`: one "is this key usable?" command, whether run on take or by `qasight pool status --check`.
4. **Human-run changes.** `qasight pool add / remove / sweep` run the user's `provider.create` and `provider.destroy`:
    - a dry run unless `--apply`, which asks first unless `--yes`;
    - refused unless the verb is under `provider.allow` (which then needs `list`, `name_prefix`, and for `create` `max_items`), and when `TESTO_PROVIDER_ACTIONS=0`;
    - at most 5 keys per command; `add` stops at `max_items` (pool `items` plus what `list` shows);
    - `destroy` only on names with `name_prefix` that `list` shows, that nobody holds and that aren't in `items`;
    - nothing runs if `list` fails.
5. **Every command is logged.** Table `resource_action` (pool, key, verb, user, host, command, ok, detail, time) in the lease database, or on the team server through `POST /api/v1/resources/actions` (a write: token or `runner`; the account name wins over the client's). `GET /api/v1/resources/actions`, `qasight pool log` and a "Provider commands" card on the Resources page show it. If logging fails, the command stops before running anything else.
6. **The config stays the source of truth.** `add` never edits `qasight.yaml`; the person adds new keys to `items`, and takes a key out of `items` before removing it.

## Options considered

- **Stop at fixed pools** (no provider). Safe, but drift and leftovers stay invisible until a test fails on a missing VM.
- **Automatic dynamic pools** (create when the pool runs dry, destroy when a lease ends). This is how Testcontainers and cloud autoscalers work. Rejected: it breaks the owner's rule even if off by default, and brings quotas, cost and orphan handling.
- **A Python provider class** (`provider: module:Class`). Possible later under the same rules; shell commands cover the users today.

## Consequences

- Nothing changes for a pool without `provider:`. `qasight pool status` on such a pool shows lease states and `?` for existence.
- A failing `provider.list` is reported (exit `3`), never guessed from: existence shows `?`.
- Leftovers are only reported by `status`. Removing them is a person's job: their own scripts, or `qasight pool sweep --apply`.
- Adding a VM is two steps (`qasight pool add --apply`, then add it to `items`). That is on purpose: no key enters or leaves a pool without a person.
