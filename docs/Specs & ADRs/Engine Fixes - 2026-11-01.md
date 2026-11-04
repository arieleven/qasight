---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-04
---

# Engine Fixes - 2026-11-01

## Context

The [Engine and CLI Bug Hunt](../engine-cli-review.md) (2026-10-31) fixed the small bugs it found and left 15 proposals (P1 to P16, no P15) that change behaviour or are bigger than a one-line fix. Taltal approved all of them on 2026-11-01. They ship as several small pull requests, one area each, so each can be reviewed and reverted alone.

## Decision

| # | Change | Where |
|---|--------|-------|
| P1 | **One run of a cycle at a time per artifacts folder.** `CycleRunService.run` holds an OS file lock on `<artifacts>/<cycle>/.lock` (`fcntl.flock`, `msvcrt.locking` on Windows) from the first stage until the reporters, native report snapshot and report archive have read the folder. A second run of the same cycle waits and prints `another run of cycle 'x' is using ... (pid N); waiting for it to finish`. Other cycles, and the same cycle with another `artifacts_root`, never wait. The OS drops the lock when the process dies, so a killed run never leaves it held. | `qasight/engine/cycle_lock.py`, `qasight/services/cycle_run.py` |
| P2 | **Stage folders a run didn't write are removed.** A stage renamed or deleted from the config left its folder (with `allure-results/`) behind, and the next run counted its old tests. At the end of a run the orchestrator deletes child folders of `<artifacts>/<cycle>/` that this run didn't write and that hold `run.log` or `allure-results/` (names compared case-insensitively, symlinks skipped). | `qasight/engine/orchestrator.py` |
| P3, P4 | **A timeout, Ctrl-C or SIGTERM stops the whole stage and records the run.** Stages start in their own process group (`start_new_session`, `CREATE_NEW_PROCESS_GROUP` on Windows) and are signalled as a group, so grandchildren (`npm` → `node`, `xdist` workers) don't outlive a timeout. The first SIGINT/SIGTERM terminates the running stages, the second kills them; no new stage starts, the run is recorded `FAILED` with `interrupted_by`, `plan_aborted` has reason `signal`, and `qasight run` exits `130` (SIGINT) or `143` (SIGTERM). | `qasight/engine/interrupt.py`, `qasight/engine/backends/__init__.py`, `qasight/engine/executor.py`, `qasight/engine/exit_codes.py` |
| P5 | **Keys nothing reads are warned about.** The raw config is walked against the JSON Schema; an unknown key prints a warning with a did-you-mean (`env` → `extra_env`, `timeout` → `timeout_s`), and `qasight config validate --strict` exits `2` on one. | `qasight/config/unknown_keys.py` |
| P6 to P8 | **Safe `on_release`, shell-style `extra_env`, strict `if:`.** A pool's `on_release` hook runs only after a heartbeat confirms the lease is still held; `extra_env` turns `true`/`false`/`null` into `true`/`false`/empty instead of Python's `True`/`None` and refuses lists and maps; an `if:` outside the supported grammar is a config error at load time instead of silently true. | `qasight/resources/client.py`, `qasight/config/if_expr.py`, `qasight/config/sections.py` |
| P9, P10, P16 | **Clear CLI outcomes.** `qasight split --index` exits `1` for an empty shard (`--allow-empty` exits `0`), since an empty `pytest $(qasight split ...)` runs every test; `qasight badge` and `qasight flaky` name the closest recorded cycle; a `--server` that isn't an `http(s)://` URL exits `2` in `split`, `ingest` and `lease`. | `qasight/cli/commands/split.py`, `qasight/cli/cycle_hint.py`, `qasight/cli/server_url.py` |
| P11 to P14 | **Real run times, no escaping attachments, Windows Jest paths, XML whitespace.** Engine runs store their real `start_time`/`end_time`; an Allure attachment symlinked outside its folder is skipped; Windows paths in Jest reports are made relative like POSIX ones; whitespace before `<?xml` is stripped. | `qasight/persistence/db_backend.py`, `qasight/reporting/allure_passthrough.py`, `qasight/reporting/jest_import.py`, `qasight/reporting/junit_import.py` |

Each row is one pull request.

## Consequences

- P1: two CI jobs of one cycle on one runner now run one after the other instead of overwriting each other's results. The API already refused a second run of a busy cycle in its own process; the lock also covers the CLI next to the API. With `--async-report-db` the background archive reads the folder after the lock is released, so a run that starts right then can still race it; per-run folders (`artifacts/<cycle>/<run-id>/`) would remove that and are left for later.
- P3/P4: `130` and `143` are new exit codes (an additive change to the frozen exit-code contract); scripts that treat any non-zero as failure are unaffected.
- P5 and P8: a config that used to load silently may now warn (P5) or fail to load (P8, a bad `if:`), which is the point: both hid mistakes.
- P2: a folder under `artifacts/<cycle>/` that holds `run.log` or `allure-results/` and isn't a stage of the current run is deleted when the run ends. Earlier runs keep their copy in `static/history/<run_id>/`; only the shared working folder is cleaned.
