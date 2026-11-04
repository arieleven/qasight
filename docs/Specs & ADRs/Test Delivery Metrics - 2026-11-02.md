---
type: spec
status: current
created: 2026-11-02
updated: 2026-11-04
---

# Test Delivery Metrics - 2026-11-02

## Context

Taltal asked whether the reports miss a metric or insight users would benefit from. The research (Allure TestOps, ReportPortal, Datadog CI, BuildPulse, Trunk, Launchable, Currents, DORA) found that QASight answers "what is broken, flaky or slower now" well, but not **how long tests stay red, what the suite costs, and whether red builds can be trusted**. It ranked thirteen metrics (M1 to M13), almost all computable from history already stored. On 2026-11-02 Taltal chose to build them in four waves:

1. M1 time to fix and failure age, M2 suite and CI time, M5 default-branch health.
2. M3 real failures vs noise, M4 cost of flakiness.
3. M6 stability per owner, M7 suite growth and hygiene.
4. The rest (failure concentration, coverage trend, retry rate, duration spread, tests that catch bugs, when failures happen).

This note records each wave as it lands.

## Decision

### Wave 1: time to fix, suite time, default branch health

`qasight/services/delivery_metrics.py` computes everything from runs (time, exit code, branch, pull request target, commit, stage timings) and per-test results (the `test_result` rows):

- **Time to fix.** A test that starts failing opens a red spell; the first run where it passes again closes it. The period's median closed spell is the time to fix, against the period before. Spells still open are **open failures**, bucketed by age (under a day, 1 to 7 days, 1 to 4 weeks, over a month), with the oldest listed. Flaky tests (by the [Better Flaky Signal](Better%20Flaky%20Signal%20-%202026-11-01.md) rules) are left out, and so is a pass on the commit that failed, which is a retry, not a fix. A test that disappears or is skipped stops counting as open. Red spells are traced back up to 90 days before the period.
- **Suite and CI time.** Each run's wall time: median and slowest tenth per cycle, against the period before; all runs together as CI minutes; and the stage whose median time grew most (beyond the same noise floor "got slower" uses).
- **Default branch health.** The share of the period the default branch was red (from a red run to the next green one, measured from the period start, or from its first run if it has none earlier), the longest red spell, and the share of pull request commits that passed on their first run. The default branch is the most common pull request target (`ci_base_ref`), else `main`, `master` or `trunk` when runs were recorded there. Time to fix and red time look at the default branch only, so work in progress on a feature branch doesn't count. When no run recorded a branch at all, every run counts.

Where it shows:

- `GET /api/v1/report` has a `delivery` object, and its trend points carry `median_duration_ms` and `ci_minutes`. The `/report` page has a **Speed and stability** row (time to fix, open failures, red time, CI time), **Suite time by day**, **Open failures by age** with the oldest ones, and **Speed and stability by cycle**. The summary sentences mention them, so email, slides and the spreadsheet carry them too.
- `GET /api/v1/dashboard/delivery?days=7` feeds the dashboard's **Last 7 days** card. The static demo serves it from `dashboard/delivery.json`.

### Wave 2: real failures vs noise, cost of flakiness

`qasight/services/failure_signal.py`:

- **Why runs were red.** Each red run in the period gets one cause from what made it red: the cause someone marked on its failures (triage memory, `services/triage.py`, matched by message signature), the flaky classification, or nothing. The most decisive wins: product bug, test bug, environment, other, not marked yet, then flaky, so a run counts as flaky only when every failure in it was a flaky test. A red run with no failing test is "crash or setup". The report gives the counts, the share that were real bugs, and the share that was noise (test bugs, environment, flaky, crashes), against the period before. Flakiness is judged over both periods together.
- **Cost of flakiness.** Per flaky test: the time of its passing retries plus its share of the wall time of red runs that only flaky tests caused (someone reran those jobs), the number of such runs, and the branches they held up. Runs whose failures were marked as another cause are not charged. `flaky_service.flaky_costs()` does the sum for both the report and the cycle page.

Where it shows: the `/report` page's **Why runs were red** (a bar by cause and the counts) and **Cost of flakiness** (CI minutes, red runs, branches held up, the most costly tests), the `signal` object of `GET /api/v1/report`, two summary sentences, and on the cycle page's flaky card a cost badge per test, which is now sorted most costly first (`red_runs` and `cost_minutes` on `GET /api/v1/analytics/flaky`).

### Wave 3: per-team view, suite growth and hygiene

- **By team** (`services/team_view.py`). A test's owners are its CODEOWNERS owners (when the cycle has a checkout, [the same lookup as the flaky card](Flaky%20Test%20Issues%20-%202026-11-01.md)) plus its `owner` labels; tests with neither count as "(no owner)", and a test with two owners counts for both. Per owner: tests in the latest run, failing now, open failures and the oldest one's age, median time to fix, flaky tests and their cost. With no owners at all, the report says how to add them instead of showing one "(no owner)" row.
- **Suite size and hygiene** (`services/suite_hygiene.py`). Tests in each cycle's latest run against the latest run before the period, with the tests added and removed (by key, so a rename is one of each; a new cycle counts from zero). Tests skipped in every run of the period (at least three runs). Tests in the slowest tenth of their cycle that passed every run over both periods: not wrong, but the first place to look when the suite is too slow.

Both are in `GET /api/v1/report` (`teams`, `hygiene`), on the `/report` page (**By team**, **Suite size**, **Skipped in every run**, **Slow and never failed**) and in the summary sentences.

### Wave 4: the smaller follow-ups

`services/failure_patterns.py`, the `patterns` object of `GET /api/v1/report`:

- **Where failures concentrate**: the fewest tests that cause 60% of the period's test failures, and the error messages repeated most across tests (by message signature, the one triage memory uses).
- **Retry rate by day**: the share of executed tests that passed only on a retry, against the period before.
- **Line coverage by day**: the last coverage recorded each day (from `--coverage`), as a line.
- **Unstable timing**: tests whose 95th-percentile time is at least three times their median and a second slower. The test page also gives each test's slowest-runs time (`p95_duration_ms` on the test summary) and flags unstable timing.
- **When failures happen**: red runs by hour (UTC), weekday and environment, with a sentence when one slice (five runs or more) fails at least twice as often as the rest.
- **Tests that caught real bugs, and tests that only raised false alarms**: failures per test marked as a product bug against those marked test bug, environment or flaky.

A true escape rate (bugs found in production that no test caught) needs bug data from an issue tracker, so it is left out.

## Consequences

- No new tables or settings: the metrics are read-side only and recomputed on each request from up to 5000 recent runs (the manager report's existing scan limit) and at most 400 runs of test results per cycle.
- A project that never sends branch data gets red time over all runs, which is right for a single-branch setup and noisy when feature branches run too. `ci_base_ref` (sent by the GitHub Action, GitLab template and `qasight ingest` in CI) fixes it.
- Time to fix needs per-test results; a cycle whose runs have none shows red time and suite time only.
- The real-bug share is only as good as the causes people mark. Unmarked failures are shown as their own group, not guessed, and the page says how to mark them.
- A team server without a checkout has no CODEOWNERS file, so only `owner` labels name teams there.
- Cost is CI time, not people's time: a rerun someone waited on costs more than its minutes, so the number is a floor.
