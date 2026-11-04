---
type: spec
status: current
created: 2026-10-24
updated: 2026-11-04
related: "Deep Dive - Execution Logic, Team Server, Technical Debt Tracker, Project Review"
---

# Scale Runs and Stages - 2026-10-24

## Context

The [Project Review](../project-review.md) listed two scale limits (A1, A2):

- **A1.** `qasight_api/cycle_execution_manager.py` started a daemon thread for every execution. One run per cycle was enforced, but nothing capped how many cycles ran at once. On a team server, ten people pressing Run started ten sets of test subprocesses on one host.
- **A2.** `engine/orchestrator.run_plan()` ran stages one at a time, so a cycle with an independent unit stage, API stage and UI stage took the sum of all three. Item 3 in the [Technical Debt Tracker](../Testing%20Workflows/Technical%20Debt%20Tracker.md).

Both fixes had to keep one engine entered through `CycleRunService` and stay small enough to explain.

## Decision

1. **A bounded run pool in the API.** `CycleExecutionManager` owns a FIFO queue and `TESTO_MAX_CONCURRENT_RUNS` worker threads (default `2`, started on the first run). `POST /cycles/{cycle}/executions` and `/adhoc-executions` still answer `202` at once; the execution stays `queued` (a status the API already had) until a worker picks it up. A cycle that is queued or running can't be started again (`409`, as before). Nothing else changes for clients: the SSE stream waits for the run's events file the same way.
2. **Opt-in parallel stages.** A cycle can set `max_parallel_stages: N` (default `1`, today's behavior). With `N > 1`, `run_plan()` runs up to N stages at once on a `ThreadPoolExecutor`; each stage is still one subprocess, so the threads only wait on it.
   - **One writer.** Worker threads only call `run_stage()` and return its `StageResult`. Starting stages, writing `events.ndjson` and calling the renderer all happen on the calling thread, so no locks are needed and the NDJSON contract is unchanged.
   - **Order.** Stages start in config order as slots free up. `stage_finished` events are written in completion order; `PlanResult.stages` (and so `plan_result.json`, the run record and reporters) stays in config order.
   - **Failures.** `fail_fast` stops new stages from starting after the first failure. Stages already running finish (their processes aren't killed), then `plan_aborted` is written. An engine crash in one stage becomes an internal-failure result (exit `4`) without stopping the others.
   - **Isolation.** Each stage already writes to `artifacts/<cycle>/<stage>/`, so the config loader rejects repeated stage names when `max_parallel_stages > 1`. Since 2026-10-31 it rejects them in sequential cycles too (ignoring case, for macOS and Windows): the second stage reset the first one's results folder, so a failed test could vanish from the run.
   - **Output.** Live output from several stages would interleave, so parallel cycles are buffered: each stage's panel and output tail prints when it finishes. Plan duration is the wall clock, or the longest stage when a `junit` stage reports more test time than that.

## Options considered

- **An external queue (Redis + RQ/Celery) with a separate worker process.** It survives restarts and scales across machines, but it adds a service to run, a second process that has to load the engine, and more to explain. Rejected for now; the pool is the same shape (a queue in front of N workers), so moving it out of process later only changes `CycleExecutionManager`.
- **A semaphore around the existing thread per run.** Smallest diff, but every queued run would still hold a thread. Rejected for the explicit queue.
- **A dependency graph between stages (`needs:`, like GitHub Actions).** More powerful, harder to explain, and no cycle needs it yet. It can be added on top: the scheduler in `_run_parallel()` would start a stage when its dependencies are done instead of when a slot is free.
- **Parallel by default.** Rejected: stages that share a `target_repo` can fight over caches, ports or a test database, and only the cycle's author knows they are independent.

## Consequences

- The queue lives in memory, like the execution registry. A restart drops queued runs; they never started, so nothing needs cleaning up. Running ones are handled by the existing orphan cleanup.
- `TESTO_MAX_CONCURRENT_RUNS` is per server process. Running several API workers multiplies it.
- Parallel stages share the host: N stages each with `workers: 4` can start 4N test processes. The [Deep Dive](../Architecture/Deep%20Dive%20-%20Execution%20Logic.md#orchestrator-level--opt-in-parallel-stages) says so.
- Tests: `tests/unit/test_cycle_execution_manager.py` (cap, FIFO, no double queueing, env parsing), `tests/unit/qasight/engine/test_orchestrator_parallel.py` (cap, config order, `fail_fast`, crash isolation, duration, single renderer thread) and `tests/unit/qasight/test_parallel_stages_config.py` (parsing, unique names, JSON Schema).
