---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-04
related: "Command Reference, Flaky Test Quarantine - 2026-10-22, Feature Gaps and Quick Wins - 2026-10-31"
---

# Better Flaky Signal - 2026-11-01

## Context

`qasight flaky` called a test flaky when, across a cycle's last runs, it both passed and failed and flipped at least twice. Gap G1 in [Feature Gaps and Quick Wins - 2026-10-31](../Roadmap%20&%20Strategy/Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md) found three problems with that. Flips were counted across every branch, so on a team server a pull request that broke a test and then fixed it, between passing runs on `main`, read as flaky. A test that failed and then passed on a retry inside one run (JUnit reruns, Surefire `flakyFailure`, pytest-rerunfailures) wasn't flaky until it also flipped across runs. And a test that started failing every run after flipping a few times stayed on the flaky list, which invites quarantining a real failure.

## Decision

`qasight/services/flaky_service.py` `classify()` marks a test flaky when any of these hold within the window, and records each as a plain-words reason (`FlakyTest.reasons`):

1. **Passed on retry.** A run's result has `statusDetails.flaky` (set by the JUnit, Surefire, Playwright, Jest and xcresult importers when a test failed and then passed). `report_archive_diff._load_cases` now reads it as `retried`. One such run is enough.
2. **Same commit, both outcomes.** It passed in one run and failed in another of the same commit (`CompletedRunView.commit`).
3. **Flips on one branch.** Outcomes are split into one lane per branch (runs with no branch share one lane), and the test needs at least two flips on one lane. Without branch data this is the old rule.

A test that failed each of its last `BROKEN_STREAK` (3) runs on its latest run's branch is **broken**: `classify()` returns `None` and `broken()` returns a `BrokenTest`. `FlakyReport.broken`, `GET /api/v1/analytics/flaky` (`broken`), `qasight flaky` (a "Broken, not flaky" table, and `broken` in `--json`) and the cycle page's Flaky tests card list them apart. `test_history`, `run_failures` and alerts use the same `classify()`, so a broken test isn't labelled flaky on the run page either.

Callers that have the run pass `flaky_service.observation(run, case)` so `classify()` sees the branch and commit; `run_failures` already filters to the run's branch lineage and adds only the commit.

## Options considered

- **Count flaky only on the default branch** (GitLab's "failed n times in main"). Rejected as the only rule: many cycles only run on pull requests. Per-branch lanes give the same answer for `main` and still work for branch-only cycles.
- **A minimum number of runs before calling a test flaky.** Not needed: a pass on retry is a stronger signal than any run count, and the flip rule already needs three runs on one branch.

## Consequences

- Fewer false "flaky" results on team servers with many branches, and flaky tests found in the first run that retries them.
- `flip_rate` is flips over the steps inside each branch's lane, so it can differ slightly from before when runs record branches.
- `qasight flaky --fail-on-flaky` no longer fails on a test that is simply broken; the run itself already fails on it.
