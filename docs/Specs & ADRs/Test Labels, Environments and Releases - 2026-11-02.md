---
type: spec
status: current
created: 2026-11-02
updated: 2026-11-02
---

# Test Labels, Environments and Releases - 2026-11-02

## Context

On 2026-11-02 Taltal asked whether Testosterone misses a key feature next to Allure TestOps and similar tools (TestRail, ReportPortal, Qualflare, BrowserStack, Datadog, Buildkite, Launchable, Xray, Zephyr). The comparison built on [Feature Gaps and Quick Wins - 2026-10-31](../Roadmap%20&%20Strategy/Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md) and [Paid Features to Offer Free - 2026-10-31](../Roadmap%20&%20Strategy/Paid%20Features%20to%20Offer%20Free%20-%202026-10-31.md) and found one real gap: Testosterone didn't know what a test is *about*. Every tool above keeps a test's feature, story, severity, owner, tags and issue links and builds its trees, filters and dashboards on them. Testosterone received those labels (Allure results, Playwright tags, JUnit properties) and threw them away.

The comparison ranked five gaps, and Taltal chose to build all of them in waves:

| Id | Gap | Status |
| --- | --- | --- |
| N1 | Test labels and issue links | Built (this note) |
| N2 | Environments per run, pass and flake rate split by environment | Planned |
| N3 | Run a chosen set of tests | Planned |
| N4 | Releases: runs tagged with an app version, a release page | Planned |
| N5 | Which issues have red tests | Planned |

Manual test case management stays out of scope, as the [comparison page](../Compared%20With%20Other%20Tools.md) says.

## Decision

### N1: test labels and links

- **One parser.** `testo_core/reporting/allure_cases.py` reads `labels` and `links` off each Allure result, next to name and status. Labels that only say where a test lives (package, suite, test class, host, thread, framework, language) are dropped; the rest (feature, story, epic, severity, owner, tag, layer and custom ones) are kept as `{name, value}`, lower-case names, at most 20 per test. Only `http(s)` links are kept (at most 10), so a `javascript:` URL in a result never reaches the UI.
- **Importers fill them.** A JUnit `<testcase>`'s own `<properties>` (pytest's `record_property`, Surefire) become labels: `tag`/`tags` split on commas, an `http(s)` value becomes a link, and `issue`/`jira`/`story`/`requirement`/`tms` without a URL stay an `issue` label. Playwright annotations whose description is a URL become links (`type: "issue"` is an issue link). Playwright tags, allure-pytest markers and allure-behave tags were already Allure labels.
- **Stored per test.** Revision `0006` adds nullable JSON columns `labels` and `links` to `test_result`. Rows from before keep `NULL`, read as no labels. Runs recorded before this change gain labels only if their snapshot is parsed again.
- **Read side.** `TestSummary` (test list and test page) and `FailingTest` (run page) carry `labels` and `links` from the latest run. `GET /api/v1/analytics/tests` takes `label=` (`feature:Checkout`, or a bare value such as `smoke`, case-insensitive) and `q=` also matches label values. `GET /api/v1/analytics/labels?cycle=` (`services/test_labels.py`) counts each label's tests, failing and flaky ones, features first.
- **UI.** Label chips and issue links on the run page's failing tests and the test page; a chip links to the cycle page with `?label=`, where the Tests card lists only those tests. A "By label" card on the cycle page shows the counts and is hidden when no test has a label. The static demo export writes `labels/<cycle>.json`; the UI filters by label on the client, so the static site works too.
- **Plain names.** The UI and API say *labels* and *links*, never a competitor's feature name.

## Consequences

- A suite that already uses Allure, Playwright tags or JUnit properties gets the breakdown with no config change. A plain pytest JUnit file has no labels until tests call `record_property("feature", "Checkout")` or use allure-pytest.
- `test_result` rows are a little larger (two small JSON values per test).
- N5 builds directly on the `issue` labels and links stored here.
