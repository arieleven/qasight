---
type: spec
status: current
created: 2026-09-25
updated: 2026-11-05
related: "Architecture Overview, Deep Dive - Execution Logic, QA Strategies"
---

# Command Adapter and JUnit Import - 2026-09-25

## Context

The first external adopter, CarBiz (an Expo app with a Python/AWS backend), wants one orchestrated run across pytest suites, **Jest**, **Playwright** and, later, **Maestro**. Before this change QASight only ran pytest, Behave and BehaveX. Any other runner could not be a stage, and even a wrapper script would produce no Allure results, so it counted as an empty stage in summaries and health %.

## Decision

1. **New equipment `command`** (`qasight/frameworks/command_adapter.py`). `args` is the complete argv, run verbatim in `target_repo`. Nothing is injected (these runners have no Allure flag). `TESTO_SHARED_ALLURE_RESULTS_DIR` is still exported for runners that can write Allure JSON themselves. `args` is required at load time. The default tier is `unit`; set `tier:` explicitly for e2e suites.
2. **New stage key `junit_xml`** (string or list of globs, relative to `target_repo`, valid on every equipment). After the process exits, the executor converts each matching file into one Allure `*-result.json` per `<testcase>` (`qasight/reporting/junit_import.py`):
   - Status: `<failure>` → failed, `<error>` → broken, `<skipped>` → skipped, otherwise passed.
   - `statusDetails` carries the message and a trace capped at 20k characters.
   - `historyId` = sha256(stage name + full name), so trends line up across runs.
   - Timing comes from the suite `timestamp` plus the per-case `time`, laid out sequentially.
3. **Safety.**
   - Patterns must be relative and cannot use `..`. Matches that resolve outside `target_repo` (for example symlinks) are skipped.
   - Files older than the stage start are ignored, so a stale report never masquerades as this run.
   - Parsing uses stdlib expat, which does not resolve external entities.
   - The import is best-effort, like the BehaveX report hook: the exit code still decides pass/fail, and the outcome is appended to `run.log`.

## Options considered

- **Copy the JUnit XML into `allure-results/` and let Allure read it.** Rejected. Allure's HTML would show the tests, but `allure_results.parse_collected_results` only reads `*-result.json`, so summaries, health %, the DB archive and the dashboard would still see an empty stage.
- **A dedicated adapter per tool (Jest, Playwright, ...).** Deferred. Every one of them already emits JUnit, so one generic path covers them all. Per-tool adapters only make sense for native report bundles (`native_report`), which can be added later without changing this contract.
- **Fail the stage when the report is missing.** Rejected. A crashed runner already exits non-zero, and a passing run with a misconfigured glob should be visible (the `run.log` line) without masking the real result.

## Consequences

- `SUPPORTED_FRAMEWORKS` gains `command`, and `Stage` gains `junit_xml` (default `()`, so existing configs are unchanged).
- Docs updated in the same change: [Architecture Overview](../Architecture/Architecture%20Overview.md) (adapter list), [Deep Dive - Execution Logic](../Architecture/Deep%20Dive%20-%20Execution%20Logic.md) (post-stage hook), [QA Strategies](../Testing%20Workflows/QA%20Strategies.md) (config example), `CHANGELOG.md`.
- Tests: `tests/unit/qasight/test_command_adapter.py`, 14 cases covering config validation, the adapter, the status and label mapping, malformed, stale and escaping files, and real subprocess stages through `run_stage`.

## Follow-ups

- Coverage aggregation per cycle (`fail_under`) and flaky-test tracking, which the CarBiz pilot needs next (see the CarBiz vault note "Test Strategy - 2026-09-25").
- A Maestro example cycle once CarBiz's native flows exist.
