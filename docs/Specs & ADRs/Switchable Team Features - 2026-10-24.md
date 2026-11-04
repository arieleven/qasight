---
type: spec
status: current
created: 2026-10-24
updated: 2026-11-04
related: "Team Server, Scale Runs and Stages, Project Review"
---

# Switchable Team Features - 2026-10-24

## Context

The fourth [Project Review](../project-review.md) pass left two things between the project and a 10: queued runs live only in memory, so a server restart drops them, and the team server has one shared token, so there are no per-person accounts, roles or audit trail. Both are what a bigger team wants. Neither should cost anything for one developer running `qasight` on a laptop or a small team sharing one token, which is most users today.

## Decision

Each feature is a switch with today's behaviour as the default. Turning a switch off returns exactly the old code path.

### Run queue: `TESTO_RUN_QUEUE=memory|db` (default `memory`)

- `memory`: unchanged. Queued runs are in the in-process FIFO only.
- `db`: `CycleExecutionManager` also writes each queued run as a row of `queued_run` in the run-history database (SQLite or Postgres, whatever `DATABASE_URL` points at). The row holds a `RunRequest` (`qasight_api/run_queue.py`): the cycle or ad-hoc stage and its options, as JSON.
- **Lifetime.** A worker deletes the row as it starts the run (`claim`). So the table only holds runs that have not started, and a run that was running when the process died is not repeated (the orphaned-run cleanup already marks it failed in the history). Repeating half-finished test runs automatically was judged worse than failing them visibly.
- **Restart.** The API's startup hook calls `recover_queued()`, which queues every row again under its old execution id, oldest first, so a client that was polling that id carries on.
- **Two processes.** `claim` is an atomic `DELETE`; if two processes both re-queued a row, only the one whose delete removed it runs it. One server process is still the supported setup: the one-run-per-cycle guard and `TESTO_MAX_CONCURRENT_RUNS` are per process.
- The in-process FIFO stays the source of order. The table is only the durable copy, so there is one queue implementation, not two.

### Accounts: `TESTO_AUTH=token|accounts` (default `token`)

- `token`: unchanged. One `TESTO_API_TOKEN`, and `/login` trades it for a session cookie.
- `accounts`: people sign in at `/login` with a username and password. Accounts live in the run-history database (`user_account`): password hashed with `scrypt` (stdlib, salted), a role, and a session version that signs that user out everywhere when bumped. Managed with `qasight users add|list|set-role|remove|reset-password`, which talk to the database directly, so there is no sign-up page to attack.
- **Roles.** `viewer` reads; `runner` also starts runs and uploads results; `admin` also changes AI settings. The bearer `TESTO_API_TOKEN` keeps working as an admin for CI and scripts, and is the secret the session cookies are signed with.
- **Audit log.** Every write by a signed-in user (or the token) is recorded in `audit_event` (time, user, method, path, status) and readable by admins at `GET /api/v1/audit`.
- `GET /api/v1/auth/me` tells the UI who is signed in and with what role.

## Options considered

- **A real queue service (Redis + RQ/Celery).** Survives restarts and scales across machines, but adds a service and a second process to run. Rejected: the database is already there and the load is a handful of runs.
- **Re-running interrupted runs on restart.** Rejected: a half-run suite may have changed shared state (a test database, deployed fixtures); failing it visibly is safer.
- **OAuth / SSO (GitHub, Google, OIDC) instead of passwords.** The right end state for a company, but it needs a public callback URL and a provider app per install. Rejected for now; `TESTO_AUTH` leaves room for an `oidc` value later.
- **Always-on accounts.** Rejected: it would make the laptop and small-team setups create a user before anything works.

## Consequences

- With both switches at their defaults, nothing changes: no new tables are used and no behaviour differs. The team server compose file shows both switches, still off.
- Both modes of each switch have tests and have to be kept working. That is the price of not forcing the team features on small users.
- In `db` mode the queued run's request (including an ad-hoc run's `extra_env`) sits in the database until the run starts.
- Tests: `tests/unit/test_run_queue.py` (round trip, claim once, restart recovery, env parsing) and `tests/contract/api/test_accounts_contract.py`.
