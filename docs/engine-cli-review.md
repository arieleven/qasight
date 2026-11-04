---
type: reference
status: current
created: 2026-10-31
updated: 2026-11-04
---

# Engine and CLI bug hunt, 2026-10-31

A hands-on pass over the Python side: `qasight` commands, `qasight.yaml` parsing, the engine (`qasight/engine/`), storage and history, resource leases and the result importers behind `qasight ingest`. Everything below was **reproduced** by running the CLI in a scratch folder with odd input. Nothing here is a guess unless it sits under "Suspicions". The earlier whole-repo scorecard is the [Project Review](project-review.md); this note is narrower and more concrete.

Severity: **High** loses data or gives a wrong verdict silently, **Med** misleads or wastes a lot of disk or time, **Low** is a rough edge.

## Fixed in this pass

| # | Sev | What happened | Fix |
|---|---|---|---|
| F1 | High | `qasight clean --yes` with `artifacts_root: .` deleted the whole project, then crashed. `--temp-dir` took `~` or any absolute path. | #219 refuses root, home, the current folder, the config's folder, their parents, and folders holding `.git` / `qasight.yaml` / `pyproject.toml` |
| F2 | High | One report with `time="inf"`, `"duration": Infinity`, `1e999` or an odd JSON shape (`{"suites":[1]}`) crashed the whole import (exit 4) and lost every other file's results | #220 and #232 (importers thread): per-file catch, `OverflowError` handled |
| F3 | High | A `<testsuite>` inside a root `<testsuite>` counted 0 tests, exit 0, so its failure was hidden | #220 |
| F4 | Med | `qasight ingest` of files that all failed to parse (empty, truncated, an HTML error page) passed with 0 tests | #220: exit 1 |
| F5 | Med | Two stages with one name (or `Unit` / `unit`, one folder on macOS and Windows): the second reset the first's results, so a failed test vanished while the run said exit 1 | #223: a config error |
| F6 | Med | `run.log` was opened for append and never reset. Each run's log held every earlier run, and was copied whole into every history snapshot (100 MB of output, then 200 MB, ...) | #223: reset when the stage starts |
| F7 | Med | `timeout_s: abc` crashed config load with a traceback; `timeout_s: 0` or `-1` timed every stage out instantly; `workers: 0` and `version: 99` were accepted, though the JSON Schema forbids all of them | #215 |
| F8 | Low | A mistyped command or flag (`qasight frobnicate`, `qasight run --bogus`, `qasight split --shards 0`) printed a Python traceback (`standalone_mode=False` let Click's `UsageError` escape). `qasight run` with no `--cycle` said `cycle '<unspecified>' not found` | #213: usage line + error, exit 2; #214: "pick one with --cycle" |
| F9 | Low | A stage whose `target_repo` doesn't exist said `executable not found: /nonexistent` | #215: names the folder |
| F10 | Low | Playwright JSON with a UTF-8 BOM, a report named `report[1].xml` (globbed), JUnit named `environment.xml` outside an Allure folder, `<testcase>` directly under `<testsuites>`, and one bad xcresult duration (dropped the whole file) | #218 (the rest after #220) |
| F11 | Low | `qasight run --workers 0` or `-3` was accepted. `qasight users add` with a bad name or an existing user asked for the password twice and then said no (a bare `Abort` traceback without a TTY). | #215, #228 |

## Open: proposals, not built

Ranked by impact. Each is bigger than a safe one-line fix or changes behaviour people may rely on, so it waited for a yes. Taltal approved them all on 2026-11-01; what each change does is in [Engine Fixes - 2026-11-01](Specs%20&%20ADRs/Engine%20Fixes%20-%202026-11-01.md).

| # | Sev | Problem (repro) | Proposal |
|---|---|---|---|
| P1 | High | **Two runs of one cycle at once corrupt each other.** Both write `artifacts/<cycle>/<stage>/` and reset it on start. `MODE=failed qasight run --cycle c &` then a passing run 1.5 s later: run A exits 1 but its record says 3 passed, 0 failed, and `run.log` mixes both. Two CI jobs on one runner, or `qasight watch` plus a manual run, hit this. | Short term: a lock file per cycle (`artifacts/<cycle>/.lock`, `fcntl`/`msvcrt`) so the second run waits or refuses with a clear message. Later: per-run folders (`artifacts/<cycle>/<run-id>/`), which also fixes P2. |
| P2 | High | **Stale stage folders count in later runs.** The report collector reads every folder under `artifacts/<cycle>/`. A stage skipped by `if:`, renamed or removed keeps its old results: run 2 exits 0 / PASS while its archive row says 2 tests, 1 failed. | Collect only the stages this run actually ran (pass the names from `PlanResult` to `collect_results` and `aggregate_cycle_metrics`). Small, but it touches the report archive, so it gets its own PR and review. |
| P3 | Med | **A timeout leaves grandchildren running.** `sh -c 'sleep 100 & wait'` with `timeout_s: 2`: exit 124, but `sleep` keeps running after `qasight` exits. Only the direct child is signalled. | Start each stage in its own process group (`start_new_session` on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows) and kill the group. Must ship with P4, or Ctrl-C stops reaching the stage. |
| P4 | Med | **SIGTERM / Ctrl-C orphans the stage and records nothing.** Killing `qasight` mid-stage leaves the test process running, the event stream ends at `stage_started`, and no run row is written. With parallel stages, Ctrl-C waits about 18 s. CI cancel buttons send SIGTERM. | A signal handler that terminates the running stage groups, records the run as interrupted, and exits 130 / 143. |
| P5 | Med | **Unknown keys are silently ignored**, even by `qasight config validate`. `timeout: 1` (meant `timeout_s`) and `env:` (meant `extra_env`) do nothing. | Warn on unknown keys with "did you mean" (difflib against the schema's keys) in `validate` and `run`; an error under `qasight config validate --strict`. |
| P6 | Med-Low | **`on_release` can reset a key someone else now holds.** A lease that missed its heartbeat (ttl 1 s, paused 1.5 s), then B takes the key, then A releases: A's `on_release` hook still runs on B's key. | Check the lease is still ours (one heartbeat) before running `on_release`; skip with a warning if it isn't. |
| P7 | Low | `extra_env` values go through `str()`: `null` becomes `"None"`, `true` becomes `"True"`, a list becomes `"[1, 2]"`. | Reject lists and mappings, map `null` to `""` and booleans to `"true"` / `"false"`. |
| P8 | Low | `if:` misparses an env value containing `==` (`${env:X}` with `X=a==b` is False), and silently evaluates `and` / `or`, which it doesn't support. | Interpolate after splitting on the operator, and reject expressions the grammar doesn't have. |
| P9 | Low | `qasight split --index N` prints nothing and exits 0 for an empty shard, so `pytest $(qasight split ...)` runs **every** test on that shard. | Exit 1 with "nothing to run on this shard" (the command already uses 1 for "nothing to split"), or document a `|| true` pattern. |
| P10 | Low | `qasight badge` / `qasight flaky` on a misspelt cycle say "no runs" (badge exits 0), the same as a real cycle before its first run. | Add "(no cycle by that name has any runs; known: ...)" to the message. Keep exit 0, since a badge job before the first run shouldn't fail. |
| P11 | Low | Engine runs store `start_time` as the persist (finish) time and `end_time` as NULL, so ordering by start is really by finish. | Pass the plan's real start and end into `RunRecord`. |
| P12 | Low | Allure attachments that are symlinks are followed, so a results folder can copy `/etc/hostname` into the artifacts. Local only; server uploads are plain bytes. | Refuse attachments whose resolved path leaves the results folder, as report files already are. |
| P13 | Low | Windows paths in Jest reports (`C:\ci\repo\src\a.test.js`) aren't made relative on Linux, so a CI report and a local one give different test ids. | Treat drive-letter and backslash paths as absolute when relativising. |
| P14 | Low | Whitespace before `<?xml` makes a report unreadable ("XML declaration not at start"). | Strip leading whitespace before parsing. |
| P16 | Low | `qasight ingest --server not-a-url` (and `split`, `lease`) exits 3 "could not reach", as if the server were down. | Check the URL has `http(s)://` and exit 2 for a malformed one. |

## Suspicions (not reproduced)

- A NUL byte in a failing stage's log tail would probably break the Postgres JSONB insert, so the run row is lost (only a warning is logged).
- `_failure_result` sets `artifacts_dir` to the `allure-results` folder rather than the stage folder.
- `if:` and `${env:...}` are resolved against `os.environ`, never the `parent_env` the API passes.

## Checked and fine

- Eight `qasight run` at once on one SQLite DB: no lock errors, all eight runs recorded.
- 3 MB of binary / invalid UTF-8 output and 100 MB of output in 2 s: no crash, memory bounded, tail decoded with replacement characters.
- XXE and billion laughs: expat refuses external entities and stops amplification, so the file is skipped cleanly.
- Path traversal in cycle and stage names, `junit_xml` patterns, Allure attachment `source` and Playwright attachment paths (POSIX and Windows forms): refused.
- 50,000 test cases with emoji and 500-character names: correct counts in 26 s.
- `flaky`, `split`, `summary`, `report list` on an empty history: clean messages.
- Lease take race (the IntegrityError path) and heartbeat refusal after expiry.
- Quarantine never waives a timeout or a non-1 exit code.
- Missing or empty config, YAML syntax errors, empty `stages:`, unknown framework, bad `args` type: clean config errors, exit 2.
