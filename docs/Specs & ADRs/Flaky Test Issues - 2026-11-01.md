---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-04
related: "Command Reference, CI-CD Pipeline Setup, Flaky Test Quarantine - 2026-10-22, Alerts Where Developers Look - 2026-10-22"
---

# Flaky Test Issues - 2026-11-01

Part of the mainstream-adoption work ([Specs & ADRs](README.md)); gap G3 in [Feature Gaps and Quick Wins - 2026-10-31](../Roadmap%20&%20Strategy/Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md).

## Context

Trunk, BuildPulse and Datadog open a ticket when a test turns flaky, assign it to the test's owner and close it once the test is fixed. QASight already found flaky tests (`qasight flaky`), knew their owners (CODEOWNERS, `qasight/services/test_ownership.py`) and offered a prefilled new-issue link, but someone had to click it. The GitHub Action's `flaky-issues` input opens issues, but only inside that action, matches them by title, and never closes them.

## Decision

1. **Off unless configured, by environment.** `TESTO_ISSUES=github` turns it on, like the alert webhooks. Token from `TESTO_GITHUB_TOKEN` or `GITHUB_TOKEN`; repository from `TESTO_ISSUES_REPO`, else the `repository` the cycle's runs recorded. Options: `TESTO_ISSUES_LABELS`, `TESTO_ISSUES_ASSIGN`, `TESTO_ISSUES_CLOSE_AFTER`, `TESTO_UI_URL`, `GITHUB_API_URL`. The variables are listed in [Command Reference § qasight issues sync](../CLI%20Commands/Command%20Reference.md#qasight-issues-sync).
2. **One hook, in `CycleRunService`.** After a persisted run's reporters, `flaky_issues.sync_after_run` does one pass for the run's cycle. Every way a run is recorded (`qasight run`, `qasight ingest`, the server's ingest and runs) goes through `CycleRunService`, so there is no second path. `qasight issues sync --cycle C [--dry-run] [--new]` runs the same pass by hand.
3. **A hidden marker identifies issues.** The body starts with `<!-- testo-flaky:<cycle>:<test key> -->`, both parts percent-encoded so a name can't end the comment or split it. Open issues are listed by label (one call, up to 1000 issues) and matched on the marker exactly; the search API was rejected because it tokenizes the phrase and is rate-limited separately. An open issue with the same title also counts as a duplicate, so issues the action opened aren't repeated.
4. **Open** one issue per test `newly_flaky_tests` reports for the run (the hook), or per flaky test (the command), at most 5 per pass. Title `Flaky test: <name> (<cycle>)`; the body has counts, flip history, the last failure in a code fence longer than any backtick run in it, owners, the test page link and a footer. With `TESTO_ISSUES_ASSIGN=1` CODEOWNERS `@user` owners are assigned; a 422 retries unassigned.
5. **Close** an open marker issue, with a comment and `state_reason: completed`, when its test is not flaky in the last 20 runs and passed each of its last N runs (`TESTO_ISSUES_CLOSE_AFTER`, default 10). Issues without the marker are never touched.
6. **Standard library, best-effort.** `qasight/services/github_issues.py` uses `urllib.request` with a 10 s timeout, a User-Agent and `Accept: application/vnd.github+json`. The first GitHub error ends the pass as one `qasight issues:` line on stderr; the hook catches everything and never changes the exit code. No SQL and no new table: everything comes from the run snapshots `flaky_service` already reads.

## Options considered

- **Search API (`in:body "testo-flaky:..."`).** Rejected for the reason in 3.
- **Store issue numbers in the database.** Rejected: a new table for something GitHub already holds, and it goes stale when someone closes an issue by hand.
- **Replace the action's `flaky-issues`.** Deferred: the action may install an older `qasight`. Both can be on; the title match keeps them from doubling.
- **Jira and Linear.** Later, as plugins behind the same pass.

## Consequences

- "Not flaky in the last 20 runs" means an issue closes only once the flips have left that window; with the default N=10 the 20-run window is usually what decides. A test that was removed never closes its issue on its own.
- The hook makes one GitHub request per persisted run even when nothing is flaky (the list), and more only when it opens or closes something.
- Owners in the body are `@mentions`, so they are notified even without `TESTO_ISSUES_ASSIGN`.
- Tests: `tests/unit/qasight/test_flaky_issues.py` (fake client and fake opener: opens once, no duplicate, closes after N green runs, assignees and the 422 retry, dry run makes no writes, errors never raise, the CLI).
