---
type: spec
status: current
created: 2026-10-26
updated: 2026-11-04
---

# Runner Coverage - 2026-10-26

## Context

A coverage review (2026-10-26) estimated that QASight supported about 23% of test framework usage natively (pytest, Behave/BehaveX, Playwright) and could record about 94% through JUnit XML. A third of that 94% needed an extra reporter package first (`jest-junit`, a .NET JUnit logger, `gotestsum`, ...), and JUnit drops what the runner knew: `describe` blocks, retries, files that failed before any test ran, tags. The aim is for QASight to be the default results history for any test tool, so the common runners should work with the output they already write, with no extra install.

## Decision

`qasight/reporting/junit_import.py` stays the one entry for every result file (stage `junit_xml` globs, `qasight ingest`, `POST /api/v1/ingest`). It now recognises a file by its **content**, not its name, and hands it to that runner's importer. Each importer writes the same Allure result dicts, so nothing after the import changes.

| File | Written by | Importer |
|------|-----------|----------|
| JUnit XML (`<testsuite>` / `<testsuites>`, and any other XML) | most runners | `junit_import.py` |
| Playwright JSON (`suites` list) | `--reporter=json` | `playwright_import.py` |
| Jest / Vitest JSON (`testResults` list) | `jest --json`, `vitest --reporter=json` | `jest_import.py` |
| TRX (`<TestRun>`) | `dotnet test --logger trx` (xUnit, NUnit, MSTest) | `trx_import.py` |
| `go test -json` lines (`Action` / `Package` events) | `go test -json` | `gotest_import.py` |
| Robot Framework `output.xml` (`<robot>`) | `robot` | `robot_import.py` |
| Allure `*-result.json` | any Allure adapter | `allure_passthrough.py` |

- **Identity.** Each importer builds a stable `fullName`: `file › describe › title` for Jest/Vitest (paths relative to the repo, or to the report's common folder when made elsewhere), `Namespace.Class.Method` for TRX, `package.Test/subtest` for Go, Robot's long name. These are also the names the runners' own "run only this test" options take, which later rerun commands rely on.
- **What JUnit lost.** A Jest test that passed on retry is flaky; a test file that failed to load is one `broken` result; TRX data rows are one result each; a failed Go package with no failing test (build error) is one `broken` result; a Go parent test counts only when it failed on its own, so a failing subtest is not counted twice; Robot tags become Allure tags. Failures outside any test are never dropped (added 2026-10-31): a Jest/Vitest file whose hook threw while its tests passed or skipped, a Playwright spec that fails to load (top-level `errors`), a Go test that never finished, a Go 1.24+ build failure (`build-output` events), a TRX run whose host crashed (`RunInfo outcome="Error"`) and a JUnit suite that claims failures no case carries each become one `broken` result; `qasight ingest` fails when every matching file was unreadable.
- **Allure folders.** `qasight ingest allure-results/` (a folder argument stands for its files) copies each result with the attachments it names, from the same folder only. Containers and the folder's settings files are skipped without a warning.
- **Server uploads.** `POST /api/v1/ingest` keeps a file's safe name and suffix (an Allure result names its attachments by file name) and reads every stored file by content.
- **Native JavaScript stages.** New equipment `jest`, `vitest` (`frameworks/jest_adapter.py`) and `cypress` (`frameworks/cypress_adapter.py`) run the runner through `npx` and add the report QASight reads, like `playwright` does: Jest's `--json --outputFile`, Vitest's `json` reporter (and `vitest run`, never watch mode), Cypress's built-in `junit` reporter. Jest and Vitest take `--maxWorkers` from `workers`; Cypress can't parallelise without its cloud, so `workers` warns there. A stage that sets its own reporter keeps it.
- **Rerun commands for more runners.** `services/rerun_commands.py` (the run page's "Rerun these tests") also uses the `framework` label each imported result carries, so an ingested file gets its own runner's command: Jest/Vitest (test files plus `-t '^(describe path title|...)$'`), `go test -run '^(TestA|TestB)$' <packages>` (top-level tests, which rerun their subtests), `dotnet test --filter FullyQualifiedName=...` (data rows rerun their method), `robot --test <name> <root suite folder>`, and for JUnit XML from a JVM build `mvn test -Dtest=Class#m1+m2` or `gradle test --tests Class.method` when the run's target repo has a `pom.xml` or a Gradle build. The pytest guess for dotted JUnit names now needs a `test_*.py` / `*_test.py` file, the files pytest collects, so a Java class is no longer offered as a pytest module. Cypress gets none: its JUnit file does not name the spec file.
- Unknown files are skipped with a note in the stage log, as malformed JUnit always was. They never fail the stage on their own.

## Consequences

- Jest, Vitest, .NET, Go and Robot suites can be recorded with the output their runner writes by default. JUnit XML keeps working for everything else.
- Detection reads each file once more than before (a sniff, then the parse). Result files are small next to the test run.
- `historyId` still hashes the stage's tool with `fullName`, so moving a suite from JUnit XML to its runner's own format starts a new history for its tests (different names).
- Allure containers (fixtures) are not shown; QASight has no place for them.

## Addendum: RSpec, k6, XCTest and Android (2026-10-26)

A comparison with Qualflare (a hosted test-results service that lists XCTest, RSpec, k6 and Espresso among its 23+ frameworks) found these four still needed a JUnit reporter or weren't covered. Same pattern, same entry point:

| File | Written by | Importer |
|------|-----------|----------|
| RSpec JSON (`examples` list + `summary`) | `rspec --format json` | `rspec_import.py` |
| k6 summary (`metrics` + `root_group`) | `k6 run --summary-export=...`, or `handleSummary` writing `JSON.stringify(data)` | `k6_import.py` |
| `xcresulttool` JSON (`testNodes` tree) | `xcrun xcresulttool get test-results tests --path X.xcresult` (Xcode 16+) | `xcresult_import.py` |
| Android instrumentation JUnit (suite `device` property) | `connectedAndroidTest` (Espresso, UI Automator, Compose) | `junit_import.py` |

- **RSpec.** `full_name` is `spec file › full description`. Pending examples are skipped; errors outside examples (a spec file that failed to load) become one `broken` result. Rerun: `rspec <files> -e <description>`, with `bundle exec` when the target repo has a `Gemfile`.
- **k6.** A load test has no test cases, so each **check** and each **threshold** becomes one result (`checks › group › name`, `thresholds › metric › expression`). A check with any failed iteration is `failed` ("3 of 120 failed"); a crossed threshold is `failed` with the metric's values. Both the `--summary-export` shape (keyed by name, threshold `true` = failed) and the `handleSummary` shape (lists, `{"ok": false}`) are read. Results have no duration (each would span the whole run). No rerun command: only the whole script reruns.
- **XCTest.** The `.xcresult` bundle is a database only Apple's tool reads, so the input is the JSON `xcresulttool` exports; reading the bundle directly would need macOS. `full_name` is `Bundle/Suite/test()`, the `-only-testing:` form. A test that passed on a repetition after failing is flaky. No rerun command: `xcodebuild` also needs a scheme and destination the results don't record.
- **Android.** The Android Gradle plugin's JUnit XML already worked; suites that name a `device` are now labelled `android` (and the device as `host`), so the run page offers `./gradlew connectedAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=Class#method`. In a project with several modules that have instrumentation tests, run it on the module's own task.

Builds on [Command Adapter and JUnit Import](Command%20Adapter%20and%20JUnit%20Import%20-%202026-09-25.md), [Playwright JSON Import](Playwright%20JSON%20Import%20-%202026-10-22.md) and [Ingest and Team Server](Ingest%20and%20Team%20Server%20-%202026-10-22.md).
