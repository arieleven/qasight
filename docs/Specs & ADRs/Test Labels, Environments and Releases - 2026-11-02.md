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
| N2 | Environments per run, pass and flake rate split by environment | Built (this note) |
| N3 | Run a chosen set of tests | Built (this note) |
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

### N2: environments per run

- **One string per run.** `services/run_environment.py` stores what a run tested on as run metadata `environment`, `"browser=firefox, os=linux"`: keys cleaned to lower case and sorted, at most 8 pairs, so two runs on the same environment compare equal as plain strings. No migration: it lives in the run's metadata JSON like the CI provenance.
- **Where it comes from**, first to last: `--env KEY=VALUE` on `testo run` and `testo ingest` (merged into `TESTO_ENV` for the process, so the stages and an upload see it too); the `TESTO_ENV` variable; Allure's own `environment.properties` in the run's results. `POST /api/v1/ingest` takes an `environment` field, which `testo ingest --server` fills from the same places. The GitHub Action has an `environment` input that sets `TESTO_ENV`. Nothing is filled in automatically (no OS or Python version), because splitting runs nobody asked to split would only weaken flaky detection.
- **Flaky detection keeps environments apart.** `flaky_service` lanes were per branch; they are now per branch and environment, a same-commit conflict needs the same environment, and a failing streak counts runs of the latest run's environment. So a test that always fails on firefox and passes on chromium is a failure on firefox, not flaky. Runs without an environment share one lane, as before.
- **Run failures.** A run's failing streak and "new" baseline use runs of the same environment (or with none recorded); a pull request's target-branch baseline prefers the run on the same environment.
- **UI and API.** `environment` on `GET /api/v1/runs`, the run detail, and each point of the test history; the test history adds `environments`, pass and fail counts per environment. The run page and the cycle's runs list show it; the test page has a "By environment" card that says "Fails only on browser=firefox" when that's the case.

### N3: run a chosen set of tests

- **One selection, many runners.** `services/test_selection.py` holds a selection (test names, and the stage each ran in when it comes from a recorded run) and `narrow_plan()` turns it into the same cycle with each stage told to run only those tests: pytest node ids (or `-k` on the names when the stage's own arguments name a path, since node ids next to `tests/` would run all of `tests/`), Playwright `--grep` with `--pass-with-no-tests`, Behave and BehaveX `--name`. Other stages run as they are, with two variables a script or adapter can use: `ALLURE_TESTPLAN_PATH` (Allure's public test plan file, which Allure's own adapters for JUnit 5, TestNG, Cucumber and pytest read to run only the listed tests) and `TESTO_SELECTED_TESTS`.
- **Entry points.** `testo run --test NAME` (repeatable) and `--rerun-failed RUN_ID` on the CLI; `tests` and `rerun_failed` on `POST /api/v1/cycles/{cycle}/executions` (kept in the run queue's JSON). The UI's run page has "Run these tests again" and the cycle page's label filter has "Run these" for a cycle defined in `testosterone.yaml`; both open the cycle's run panel with the selection shown and a "Run every test instead" way out.
- **Same engine.** The narrowed plan goes through `CycleRunService` and `run_plan()` like any run; nothing else changes. The run records `selected_tests` (how many) and `selected_from` in its metadata.
- **No match is not a failure.** pytest exits 5 when it collects nothing; with a selection the executor counts that as passed for the stage, because it only means none of the chosen tests lives there. A selection from a recorded run skips stages with no failing test.
- **Not a scheduler.** Nothing here splits tests across machines or calls a CI provider's "re-run failed jobs" (F11 in the paid-features note); the run happens where `testo run` or the team server runs.

## Consequences

- A suite that already uses Allure, Playwright tags or JUnit properties gets the breakdown with no config change. A plain pytest JUnit file has no labels until tests call `record_property("feature", "Checkout")` or use allure-pytest.
- `test_result` rows are a little larger (two small JSON values per test).
- N5 builds directly on the `issue` labels and links stored here.
- A run of chosen tests is a run like any other in the history. Its absent tests are simply absent: flaky detection and per-test history skip runs a test isn't in, and Compare lists them as not run.
- Environments split flaky lanes only when runs record one. A team that sets `TESTO_ENV` on some jobs and not others gets an extra "not set" lane until every job sets it.
