---
type: spec
status: current
created: 2026-11-02
updated: 2026-11-02
---

# Test Delivery Metrics - 2026-11-02

## Context

Taltal asked whether the reports miss a metric or insight users would benefit from. The research (Allure TestOps, ReportPortal, Datadog CI, BuildPulse, Trunk, Launchable, Currents, DORA) found that Testosterone answers "what is broken, flaky or slower now" well, but not **how long tests stay red, what the suite costs, and whether red builds can be trusted**. It ranked thirteen metrics (M1 to M13), almost all computable from history already stored. On 2026-11-02 Taltal chose to build them in four waves:

1. M1 time to fix and failure age, M2 suite and CI time, M5 default-branch health.
2. M3 real failures vs noise, M4 cost of flakiness.
3. M6 stability per owner, M7 suite growth and hygiene.
4. The rest (failure concentration, coverage trend, retry rate, duration spread, tests that catch bugs, when failures happen).

This note records each wave as it lands.

## Decision

### Wave 1: time to fix, suite time, default branch health

`testo_core/services/delivery_metrics.py` computes everything from runs (time, exit code, branch, pull request target, commit, stage timings) and per-test results (the `test_result` rows):

- **Time to fix.** A test that starts failing opens a red spell; the first run where it passes again closes it. The period's median closed spell is the time to fix, against the period before. Spells still open are **open failures**, bucketed by age (under a day, 1 to 7 days, 1 to 4 weeks, over a month), with the oldest listed. Flaky tests (by the [Better Flaky Signal](Better%20Flaky%20Signal%20-%202026-11-01.md) rules) are left out, and so is a pass on the commit that failed, which is a retry, not a fix. A test that disappears or is skipped stops counting as open. Red spells are traced back up to 90 days before the period.
- **Suite and CI time.** Each run's wall time: median and slowest tenth per cycle, against the period before; all runs together as CI minutes; and the stage whose median time grew most (beyond the same noise floor "got slower" uses).
- **Default branch health.** The share of the period the default branch was red (from a red run to the next green one, measured from the period start, or from its first run if it has none earlier), the longest red spell, and the share of pull request commits that passed on their first run. The default branch is the most common pull request target (`ci_base_ref`), else `main`, `master` or `trunk` when runs were recorded there. Time to fix and red time look at the default branch only, so work in progress on a feature branch doesn't count. When no run recorded a branch at all, every run counts.

Where it shows:

- `GET /api/v1/report` has a `delivery` object, and its trend points carry `median_duration_ms` and `ci_minutes`. The `/report` page has a **Speed and stability** row (time to fix, open failures, red time, CI time), **Suite time by day**, **Open failures by age** with the oldest ones, and **Speed and stability by cycle**. The summary sentences mention them, so email, slides and the spreadsheet carry them too.
- `GET /api/v1/dashboard/delivery?days=7` feeds the dashboard's **Last 7 days** card. The static demo serves it from `dashboard/delivery.json`.

## Consequences

- No new tables or settings: the metrics are read-side only and recomputed on each request from up to 5000 recent runs (the manager report's existing scan limit) and at most 400 runs of test results per cycle.
- A project that never sends branch data gets red time over all runs, which is right for a single-branch setup and noisy when feature branches run too. `ci_base_ref` (sent by the GitHub Action, GitLab template and `testo ingest` in CI) fixes it.
- Time to fix needs per-test results; a cycle whose runs have none shows red time and suite time only.
