---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-04
related: "Flaky Test Quarantine - 2026-10-22, Command Reference, Team Server"
---

# Quarantine From the UI - 2026-11-01

Gap G2 (and quick win Q8) of [Feature Gaps and Quick Wins](../Roadmap%20&%20Strategy/Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md).

## Context

[Flaky Test Quarantine - 2026-10-22](Flaky%20Test%20Quarantine%20-%202026-10-22.md) made quarantine a `quarantine:` list in `qasight.yaml`, applied by the stage gate in `qasight/engine/quarantine.py`. That reaches `qasight run` users only. People who record results with `qasight ingest` or `pytest --qasight`, often from CI against a team server, have no config file (an ingest plan didn't even read one), so `qasight flaky` told them to paste a snippet into a file they don't have (Q8). Trunk, Buildkite and Datadog sell the alternative: a Quarantine button, a list the server keeps, and jobs that read it, so a quarantined failure doesn't fail the build while it is fixed.

## Decision

1. **A stored list.** Table `quarantined_test` in the run-history database (`QuarantinedTest` in `qasight/repository/models.py`), one row per cycle and test (id: sha256 of `"cycle\ntest"`), with `reason`, `owner`, `added_by`, `added_at`. `QuarantineStore` in `qasight/services/quarantine_store.py` (`add`, `remove`, `list`, `rules`) follows `TriageStore`: the table is created on first use. A test is a name or full name as runs record it, or an `fnmatch` glob, matched exactly like config rules.
2. **Applied in the one engine path.** `CycleRunService.run` calls `quarantine_for_run` before `run_plan`: the plan's own rules, then the rules of a config cycle with the same name (so an ingest cycle gets them too), then the stored list when the run is persisted. That covers `qasight run`, `qasight ingest`, the team server's `POST /api/v1/ingest` and API runs. `qasight ingest --server` already exits with the server's `exit_code`, so the server's list decides for CI jobs that upload there, without a second check. The GitHub Action runs `qasight run` on the job's own history (restored from the cache), so it applies that history's list and the config; a job that should follow a team server's list records with `qasight ingest --server` or `pytest --qasight-server`. The gate itself is unchanged: only exit `1` is waived, results stay as they are (mute, not skip).
3. **`pytest --qasight`.** In `pytest_sessionfinish`, when pytest exits `1`, the plugin reads the session's JUnit XML the way `qasight ingest` does (`import_junit_reports`) and runs `engine.quarantine.evaluate` with the config rules plus the stored list (this machine's, or `GET /api/v1/quarantine` on `--qasight-server`). When every failure matches, `session.exitstatus` becomes `0` and a summary line names the tests.
4. **Auto-release.** After a persisted run, next to the issues hook, `release_after_run` removes exact-name entries whose test passed on the first try in each of its last `TESTO_QUARANTINE_RELEASE_AFTER` runs (default 10, `0` off). Skipped and absent runs don't count; a pass after a retry breaks the streak; globs are never released. Best-effort, logged.
5. **API.** `GET /api/v1/quarantine?cycle=`, `PUT /api/v1/quarantine {cycle, test, reason?, owner?}`, `DELETE /api/v1/quarantine?cycle=&test=` (`qasight_api/routes/quarantine.py`). Writes go through the same app-wide guard as triage: the API token, or a `runner` account in accounts mode, whose name is stored in `added_by`. The audit middleware records API writes; `record_audit` adds CLI changes and releases (user `qasight`, method `RELEASE`) in accounts mode. `/api/v1/analytics/flaky` items carry `quarantined` (stored list, plus config when the server can read it) and `quarantine_entry` (the exact stored name the UI can release).
6. **CLI.** `qasight quarantine list|add|remove --cycle C [TEST]`, on this machine's history or `--server`. `qasight flaky` marks stored entries and suggests `qasight quarantine add --cycle C "<name>"` before the yaml snippet (Q8).
7. **UI.** `QuarantineControl` (`frontend/src/features/quarantine/`) on the cycle page's Flaky tests card and on the test page: "Quarantine", or a "Quarantined" badge with "Release" for an exact stored entry. Writes are react-query mutations through `apiClient`; a refused write (no token, viewer role) shows the API's message. The static demo shows the badge without buttons.

## Options considered

- **Write the YAML from the UI** (the 2026-10-22 note's deferred idea). Rejected: a team server usually has no checkout of each project to write to, and ingest users have no file at all.
- **Check the list on the client** in `qasight ingest --server` (fetch the list, re-evaluate). Not needed: the server already judges the upload with its list and returns the exit code. Only `pytest --qasight` judges locally, because pytest's own exit code is what the job sees.
- **Deselect quarantined tests.** Rejected for the same reason as before: no signal for when they are fixed, and auto-release needs their results.
- **Release after N days instead of N runs.** Rejected: a nightly cycle and a per-commit cycle would need different numbers of days; N clean runs means the same thing for both, like `TESTO_ISSUES_CLOSE_AFTER`.

## Consequences

- One more table, `quarantined_test`, added by Alembic revision `0005` (which also gives `test_result` a `retried` column for the "passed on retry" signal); nothing to run, the database upgrades itself on first open.
- Every persisted run reads the list once (one indexed query); a database error leaves the run its config rules and one warning line. A `--no-persist` run uses only the config.
- The two lists don't merge in either direction: `qasight quarantine list` and the API show only stored entries, and a test quarantined by `qasight.yaml` or a glob shows "Quarantined" without "Release" in the UI.
- `pytest --qasight` against a server makes one extra request per failing session; if it fails the session keeps exit `1` and says why.
- Tests: `tests/unit/qasight/test_quarantine_store.py` (store, merge, the engine path, auto-release, audit), `test_pytest_plugin.py` (waiver), `test_cli_quarantine.py`, `tests/contract/api/test_quarantine_contract.py` (routes, roles, audit, server ingest, flaky items), and the frontend's `FlakyTestsCard`, `TestPage` and `QuarantineControl` tests.
- Still open from G2/G3: a quarantined test doesn't open an issue yet, and quarantine has no expiry date.
