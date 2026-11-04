---
type: spec
status: current
created: 2026-10-31
updated: 2026-11-04
---

# Importer and Integration Bug Hunt - 2026-10-31

## Context

The result importers ([Runner Coverage - 2026-10-26](Runner%20Coverage%20-%202026-10-26.md)) and the integrations around a run (the GitHub Action, the PR comment, Slack/Teams alerts, [OpenTelemetry export](OpenTelemetry%20Export%20-%202026-10-30.md), [affected-test selection](Affected%20Test%20Selection%20-%202026-10-30.md), the plugin API) are where QASight meets files and services it doesn't control. This hunt fed them:

- more than 60 malformed and edge-case files: empty, truncated, UTF-16, BOM, billion-laughs, XXE, `inf`/`nan` times, wrong field types, 2 MB messages, HTML error pages;
- reports written by the real tools in the cloud container: Go 1.24.7 (`go test -json`: build failure, panic, `-timeout`), Jest 29, jest-junit, Vitest 3, Playwright 1.48, RSpec 3.13 and Robot Framework 7.

It looked for crashes, wrong counts and silent data loss. The integrations were checked with local stubs only: no real Slack, Teams or OTLP endpoint.

## Decision

Small, safe fixes shipped with tests, one pull request per area. Bigger items are proposals below and wait for a decision.

## Findings, ranked

Status: **fixed** means merged or in an open fix PR (number given). **proposal** means not started.

### High

| # | Area | Finding | Status |
|---|---|---|---|
| H1 | Go | Go 1.24+ reports a build failure as `build-output` events keyed by `ImportPath` before any `Package` event. The importer rejected the whole file as "not valid JSON", so `qasight ingest` recorded 0 tests and **exited 0 for every Go build failure**. | fixed #217 |
| H2 | `qasight ingest` | A truncated, empty or non-report file (a job killed mid-write) recorded a passing run with 0 tests (exit 0). | fixed #217: exits 1 when nothing was readable |
| H3 | Playwright | A spec that doesn't compile, or a failing `globalSetup`, stops Playwright before any test runs. The failure only appears in top-level `errors`, so the run was 0 tests, exit 0. | fixed #217: one `broken` result per error |
| H4 | Jest/Vitest | A `beforeAll`/`afterAll` that throws fails the *file*. The tests show passed (Jest) or skipped (Vitest), so the run was green. | fixed #217: `(test file failed)` broken result |
| H5 | PR comment | A backtick in a test name closed the code span, so `@user`/`@org/team` pings and raw HTML rendered live. In a fork PR the author controls test names. | fixed #226 |

### Medium

| # | Area | Finding | Status |
|---|---|---|---|
| M1 | All importers | One file with an unexpected field type (non-dict Jest/Playwright entries, a non-string `error.message`, RSpec `pending_message`, k6 `passes`, JUnit `time="inf"`) crashed the whole import. Every other file's results were lost: `qasight ingest` exited 4 with a traceback, and the post-stage hook logged "import failed". | fixed #232 |
| M2 | TRX | A crashed test host (`RunInfo outcome="Error"`) with no results was recorded as passed. | fixed #217 |
| M3 | JUnit | A `<testsuite errors="1">` with no failing `<testcase>` (a suite that failed to load) counted as passed. | fixed #217 |
| M4 | Go | A test that hit `-timeout` was dropped, and only "(package failed)" showed. | fixed #217: the hung test is `broken` |
| M5 | Robot | A failed suite teardown didn't fail the suite's tests. `output.xml` keeps each test's PASS, and `robot` applies "Parent suite teardown failed" when it reads the file, so `robot` exited 2 while QASight recorded 1 failure. | fixed #232 |
| M6 | JUnit | A `<testsuite>` nested under a root `<testsuite>` was read as 0 tests, and UTF-16 XML (the Windows PowerShell default) was rejected. | fixed #232 |
| M7 | PR comment | With no target-branch run, the baseline fell back to the newest run on *any* branch. A push to main was compared with a feature branch, and a PR with another PR. | fixed #226 |
| M8 | Action | When `qasight run` stopped before recording a run (bad config), the Action published the **previous job's** run (restored from cache) as this job's summary, comment, annotations and alert. `summary_json` never carried the error. | fixed #226 |
| M9 | Plugins | One reporter plugin that failed to import turned off *every* reporter, including Slack, Teams and Allure. `PluginError` is a `ValueError`, and the orchestrator swallowed it. | fixed #229 |
| M10 | Plugins | A constructor that raised crashed `qasight run` after the cycle had finished and turned a green exit 0 into 1. | fixed #229 |
| M11 | Affected | A folder rule (`tests/payments/**`) also picked `__init__.py` and helpers, so `pytest --qasight-affected` always ran everything. | fixed #229 |
| M12 | Affected | git quotes non-ASCII paths, so `tests/test_é.py` was never selected. | fixed #229 |

### Low

| # | Area | Finding | Status |
|---|---|---|---|
| L1 | JUnit | Maven Surefire reruns (`flakyFailure`, `rerunFailure`) passed without the flaky flag. | fixed #232 |
| L2 | JUnit | A report whose name contains `-attachment` (`email-attachment-tests.xml`) was skipped as an Allure attachment. | fixed #232 |
| L3 | JUnit | An HTML error page or other non-JUnit XML counted 0 tests with no warning. | fixed #232 |
| L4 | JUnit | The `message` attribute wasn't capped (2 MB stored whole). | fixed #232 |
| L5 | Summary | "No test changed outcome" was shown when the previous run had no stored results. | fixed #226 |
| L6 | Summary | The runner column wasn't escaped, a cycle name with `-->` ended the comment marker, and list items showed `\|`. | fixed #226 |
| L7 | pytest JUnit | A test that passes and then errors in teardown is written as **two** `<testcase>` entries with the same name, so it counts as 2 tests (1 passed, 1 broken). | fixed (P3) |
| L8 | Go | With `-count=2`, or reruns in one file, attempts of the same test overwrite each other, so a fail-then-pass test isn't flagged flaky. | fixed (P3) |
| L9 | Action | `cycle: all` publishes the summary, annotations and alert of the last cycle only. | fixed (P4) |
| L10 | Action | `${{ inputs.* }}` are pasted into the composite step's bash, so a `"` or `$(…)` in an input (`cycle: ${{ github.head_ref }}`) runs as shell. | fixed (P5) |
| L11 | OTel | `test.case.name` is the same for every parametrized case, and an endpoint with no scheme exits 3 instead of 2. | fixed (P6) |
| L12 | JUnit | A suite's `tests="5"` with only 1 `<testcase>` gives no warning, and a naive `timestamp` is read as local time. | count warning added (P2); naive timestamps stay local time, which is what pytest and Surefire write |

**Suspected, not verified:**
- Teams card text isn't markdown-escaped (`_x_` turns italic).
- Slack's 3,000-character cut can split an `&amp;`.
- `upsert_pr_comment` edits the first comment carrying the marker, whoever wrote it.
- A plugin raising `SystemExit` at import isn't caught.
- A "(quarantined)" tag is lost on names over 120 characters.

Belongs to the engine/CLI hunt instead: Playwright JSON with a BOM, report paths containing `[ ]`, `environment.xml` outside Allure folders, testcases directly under `<testsuites>`, and xcresult `1.2.3s` durations.

**Checked and fine:**
- Billion-laughs and XXE are refused by expat.
- Playwright attachment paths outside the repo (`/etc/passwd`, `../..`) are never copied.
- Slack/Teams with bad URLs become `AlertError`, and the URL never leaks.
- Slack escapes `<!channel>`.
- OTLP refused, 500 and wrong-path responses exit 3, and a missing `[otel]` extra exits 2 with a hint.
- RSpec errors outside examples (load errors, `after(:context)` failures) are already a broken result.
- The GitHub comment size limit can't be hit (names are cut to 120 characters, lists to 20 rows).
- A fork's read-only token only warns.

## Proposals

- **P1. Prefer runners' own formats over jest-junit.** In real output, jest-junit (default config) **dropped a test file that failed to compile** and named every suite `undefined`. The [Getting Started](../Getting%20Started.md) and setup prompts could say "use `jest --json`" first. Docs only. **Done 2026-11-01:** README, Getting Started, Team Server and Command Reference now show `jest --json`.
- **P2. Cross-check suite counts.** When a `<testsuite tests="N">` claims more cases than it holds, log a warning like the Playwright report notes. This is cheap, but some tools write wrong counts, so warn rather than fail.
- **P3. Merge duplicate attempts.** Fold same-name `<testcase>` entries in one suite (the pytest teardown error) and repeated Go runs into one result: worst status wins, and a fail followed by a pass is flaky. This changes counts users already have, so it needed a decision. **Decided 2026-11-01 (Taltal: go with the recommendation), built:** see [Decision on duplicate attempts](#decision-on-duplicate-attempts-2026-11-01).
- **P4. `cycle: all` in the Action.** Publish one summary and alert per `plan_finished` event.
- **P5. Action inputs through `env:`.** Read inputs as environment variables in the composite steps instead of `${{ }}` in bash. Also split `package` on spaces so `qasight pytest-xdist` works.
- **P6. OTel naming.** Use the test's parametrized `name` in `test.case.name`, and treat an endpoint with no scheme as bad input (exit 2).

## Decision on duplicate attempts (2026-11-01)

Taltal approved P3 as recommended. A test listed more than once **in one JUnit `<testsuite>`**, or run more than once in **one `go test -json` file** (`-count=N`), is now one result:

- a failure followed by a final pass is `passed` and flaky, with the first failure's message ("Passed on rerun. …");
- otherwise the worst attempt wins: failed, then broken, then passed, then skipped (a pass followed by a teardown error is `broken`).

The merge is by the test's history id (tool plus full name), only inside one suite or one Go file. The same name in two suites, two files or two Playwright projects stays separate. The other importers already report one result per test (Playwright and Jest keep retries inside the test).

What changes for users: totals for affected reports go down (pytest before 8 with teardown errors, `go test -count`), and a few tests that were counted as failed are now flaky passes. Old runs keep their stored counts. The shared helper is `fold_attempts` in `qasight/reporting/imported_results.py`.

P2 shipped with it: a leaf `<testsuite tests="N">` listing fewer than N cases adds a note to the import log ("the runner may have stopped while writing it"). It never fails the stage, because some tools write wrong counts.

## Consequences

- A red build outside any single test now becomes a `broken` result and a red `qasight ingest`. This adds results named `(test file failed)`, `(failed to load)`, `(test run error)`, `(suite failed)` and `(package failed)`, so totals can be one higher than the runner's own test count.
- One bad file never costs the other files' results.
- The fuzz harness and the real-tool reports are not checked in. The regression tests use the real tools' shapes, in `tests/unit/qasight/test_import_failures_not_green.py` and `test_import_hardening.py`.
