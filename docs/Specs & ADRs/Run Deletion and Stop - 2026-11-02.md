---
type: spec
status: current
created: 2026-11-02
updated: 2026-11-02
---

# Run Deletion and Stop - 2026-11-02

## Context

Run history was only ever deleted by the retention rules (`history.retention` in `testosterone.yaml`, applied by `testo clean --history` and by the API server every 6 hours). Nobody could delete runs they picked, a run started from the UI could not be stopped (Ctrl-C only reached `testo run`), and the UI did not say how long runs are kept or how long a stage may take.

The engine's stop switch (`testo_core/engine/interrupt.py`) was process-wide: right for the CLI, wrong for the API server, which runs several cycles at once on one process. Pulling it from one run would have stopped them all.

## Decision

- **Delete chosen runs** through the same code as retention: `history.retention.delete_runs()` removes a run's row, its `test_result` rows and its `static/history/<run_id>/` copy, and skips runs still `RUNNING`. Entry points: `testo clean --history --run ID`, `POST /api/v1/runs:delete`, and **Select runs** on the Runs page (with a confirm step). In accounts mode it needs the `admin` role, because it can't be undone; every call is in the audit log.
- **Stop a run** with a stop scope per run. `interrupt.StopScope` holds a run's stage processes and why it stopped; the CLI keeps one process-wide scope for the signal handlers, and each API run enters its own (`interrupt.scoped`). Parallel stage threads get a copy of the context, so they see it. `POST /api/v1/cycle-executions/{id}/cancel` terminates that run's stages (again: kills them), the rest of its stages are skipped, and it is recorded like a SIGTERM run (exit 143, `interrupted_by: a stop request`, `plan_aborted` reason `stopped`). A queued run is dropped at once. The execution's status is `cancelled`. The `runner` role can stop, as it can start.
- **Show the settings**, read-only, on the Runs page from `GET /api/v1/run-settings` (retention limits, how often the server applies them, the default `timeout_s`). They stay in `testosterone.yaml`; the UI says where to change them.

## Consequences

- One deletion path for retention and for people, so the two can't drift.
- A run started by `testo run` in another process can't be stopped from the UI; the Stop button is on the live run panels (cycle page, Quick run) of runs this server started.
- A whole-run time limit is still not a setting; `timeout_s` is per stage.
- Editable retention from the UI was left out: it would mean the server writing `testosterone.yaml`, a second source of truth.

See [Command Reference § testo clean --history](../CLI%20Commands/Command%20Reference.md#testo-clean---history), [Troubleshooting § plan_aborted](../CLI%20Commands/Troubleshooting%20and%20Error%20Codes.md#plan_aborted-engine-fail_fast) and [Deep Dive § Stopping a run](../Architecture/Deep%20Dive%20-%20Execution%20Logic.md#stopping-a-run).
