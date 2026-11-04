---
type: spec
status: current
created: 2026-10-30
updated: 2026-11-04
---

# Affected Test Selection - 2026-10-30

## Context

On a pull request most of a suite tests code the change didn't touch. Running only the tests a change can affect makes CI faster, but a selection that misses a test hides a failure until main breaks. Taltal asked for it (2026-10-30) with three conditions: map changes to tests from past coverage or configurable path-to-test globs, fall back to running everything when unsure, and show why each test was picked. Off by default.

## Decision

`qasight/services/affected_tests.py` picks the tests; two opt-in entry points use it:

- **`qasight affected`** prints the tests and the reason for each (text, `--json`, or `--list` for other runners' command lines).
- **`pytest --qasight-affected`** (or `TESTO_AFFECTED=1`) deselects the rest inside pytest, so pytest users need no script. It runs before `--qasight-shard`, so shards split only the affected tests.

**Changed files** are the working tree against the merge base with the baseline branch, plus untracked files (so a local run sees uncommitted work). `git diff --no-renames` lists both names of a moved file. The base is `--base`, else `affected.base`, else the pull request's target branch from the CI variables `qasight/services/ci_provenance.py` already reads, else `origin/main`.

**Mapping**, for each changed file, first match wins (`affected:` in `qasight.yaml`, parsed by `qasight/config/affected.py`):

| Step | Source | Result |
|------|--------|--------|
| 1 | `run_all_when` (built in: `conftest.py`, `pyproject.toml`, lockfiles, `qasight.yaml`, ...) | run everything |
| 2 | `ignore` (built in: `__pycache__/`, `*.pyc`, `.pytest_cache/`, ...) | no test |
| 3 | `test_files` name patterns (`test_*.py`, `*.spec.ts`, `*_test.go`, ...) | that file; nothing if it was deleted |
| 4 | `rules`: `changed` globs to `tests` globs or pytest node ids | those tests |
| 5 | a coverage.py JSON report with test contexts (`pytest --cov --cov-context=test`, `coverage json --show-contexts`) | the tests that ran a line of the file |

**When unsure, run everything.** A file no step maps, a file the coverage report lists but no test ran (only imported), a rule whose test globs match no file, a failed `git diff`, an unknown base and an empty diff each add a reason to `run_all_reasons`, and any reason means the whole suite. In pytest, a picked Python test that wasn't collected (renamed since the coverage report, or outside the paths pytest was given) also runs everything. Only `ignore` can make a change run nothing; pytest then exits `0` instead of its "no tests ran" `5`.

**Why each test.** Every picked test carries "`<changed file>`: `<why>`" lines; `qasight affected` prints them, `--json` returns them, and pytest prints the first 20 in its summary.

## Consequences

- Nothing changes unless a user passes `--qasight-affected` or calls `qasight affected`. `qasight run` doesn't select by itself: a stage's command line belongs to the user, and `--list` gives them the tests to put in it.
- Run history isn't a mapping source: a run records outcomes, not which files a test touched. Coverage contexts are the "past" signal, and they go stale; the run-all fallbacks (a test that isn't collected, a file the report doesn't list) catch the common cases, and a periodic full run (nightly, or on main) catches the rest.
- Coverage is pytest-only for now (contexts are pytest node ids). Other runners use `rules` and changed test files.
- Reference: [Command Reference § qasight affected](../CLI%20Commands/Command%20Reference.md#qasight-affected).
