---
type: spec
status: current
created: 2026-10-31
updated: 2026-11-04
---

# Architecture Review - 2026-10-31

[Specs & ADRs](README.md) · [Architecture Overview](../Architecture/Architecture%20Overview.md) · [Project Review](../project-review.md)

A structural review of the codebase at `8a78225` (1.5.0), done with the Claude Fable 5.1 model and the architecture review skill. It looks at module boundaries, layering, coupling, the data model and its migrations, scaling limits and extension points, and compares them with what [ARCHITECTURE.md](../../ARCHITECTURE.md) and the diagrams claim. The [Project Review](../project-review.md) scores the whole repo; this note only ranks the architecture's pressure points and recommends what to do about each. It proposes; nothing here was built.

## Verdict

The architecture is sound for what the project is today: one engine entered through `CycleRunService`, typed events as the seam between engine and renderers, adapters behind a small protocol, entry-point plugins, generated frontend types. Every feature shipped in October (ingest, team server, quarantine, triage, pools, container runner, OTel) landed without a second path, which is the best evidence the shape works.

The pressure points are all on the **read side**, not the engine. Run history is one table with a JSON blob, per-test data lives in files on disk, and most analytics load the last 500 runs into Python and filter there. That is fine for a laptop and a small team server, and it is the first thing that will break when a team points many CI jobs at one server. The second theme is that `ARCHITECTURE.md` has drifted from the code in a few places (session boundary, `services/` as "analytics", "sequential stages"), and nothing in CI stops layering from drifting further.

## Ranked findings

Ranking is by how much a future change would cost if the finding is left alone. Severity is High when it limits a documented use (team server), Medium when it costs maintainers time, Low when it is cosmetic drift.

### R1. Per-test data is files, not rows (High)

Per-test outcomes, durations and flaky signals come from each run's Allure `*-result.json` copied to `static/history/<run_id>/artifacts/` (`persistence/db_backend.py`, `_snapshot_run_artifacts`). `flaky_service`, `test_history`, `run_failures`, `project_health`, `run_summary_markdown` and `otel_export` each call `list_recent_runs(limit=500)`, filter the cycle in Python (8 such sites) and then open files per run.

Consequences today:

- The API must share a disk with every run it shows. The team server does (ingest writes locally), so it works, but the design can never put the API behind a load balancer or move artifacts to object storage without rewriting every analytics module.
- Every per-test request is O(runs × tests) file reads; the per-test page, Compare and `/health` re-parse the same JSON on every call. There is no cache.
- A cycle with more than 500 runs silently loses history: `_SCAN_LIMIT = 500` is a hard ceiling in five modules.
- Disk grows without bound. `static/history/` has no retention; `qasight clean` removes `artifacts/`, not history.

### R2. One table, one JSON column (High)

`RunRecord` has `id`, `status`, `start_time`, `end_time` and `metadata` (JSON). The cycle name, health, exit code, branch, commit, stages and the snapshot pointer are all inside the blob. There is no index on `start_time`, no `cycle` column, and the repository protocol has one list method (`list_recent_runs`) and one status filter. Every "runs of cycle X" and every dashboard rollup is a full scan of the newest N rows plus JSON decoding in Python.

This also makes the data model unqueryable from outside QASight (SQL dashboards, Grafana), which teams running Postgres will ask for.

### R3. No migrations (Medium, becomes High at the next schema change)

Schema is `SQLModel.metadata.create_all()` on startup (`repository/db_config.py`). `ReportArchive`'s docstring already says existing deployments "need a manual `ALTER TABLE`". Ten tables now exist (runs, archives, queue, users, audit, triage, four resource tables), the team server runs Postgres, and the first column added to any of them after 1.5.0 will break upgrades for everyone on a persistent database. Fixing R1 or R2 requires this first.

### R4. `services/` is the catch-all (Medium)

`qasight/services/` is 32 modules and about 7,000 lines, the largest package in the repo. It holds the use case (`cycle_run`), analytics (delta, flaky, dashboard, test history), write paths (`junit_ingest`, `triage`, `report_archive`), exporters (`otel_export`, `run_summary_markdown`, `badge`), the AI subsystem, and two review features (`project_health`, `project_review`). `ARCHITECTURE.md` describes it as "Analytics". New code has nowhere else obvious to go, so it goes here.

### R5. The session boundary moved and nobody told the docs (Medium)

`ARCHITECTURE.md` says `qasight/repository/` is "the only code that opens a database session". Four other modules now open `Session(engine)` directly: `accounts.py`, `services/triage.py`, `resources/store.py` and `qasight_api/run_queue.py`. Each is tidy on its own, but there are now five places that must agree on dialect quirks, retries and testing doubles, and the rule that made the storage layer explainable in one sentence is gone. Either the rule is reinstated or the docs should say "one engine factory, several stores".

### R6. Nothing enforces layering (Medium)

The dependency direction is mostly right (nothing in `services/`, `engine/` or `reporting/` imports `cli/`; `qasight` imports `qasight_api` only in `cli/commands/ui_cmd.py`, which is the deliberate `qasight ui` entry). But there are small inversions that will multiply without a check:

- `engine/` and `config/` import `reporting.paths` for `safe_child_path` and `plan_artifacts_dir`, which are path helpers, not reporting.
- `config/` imports `resources.pools` (three modules) and `reporting`; the configuration layer now depends on two feature packages.
- `persistence/` imports `engine`, `reporting`, `repository` and `security`.

None of this is wrong in isolation, but the layer table in `ARCHITECTURE.md` is a promise that CI does not keep. An `import-linter` contract (about 20 lines of config) would.

### R7. In-process state limits the API to one process (Medium, documented)

`CycleExecutionManager`, the login throttle, the AI settings store (file-backed, but cached per process) and the SSE file tail all assume one uvicorn worker. `TESTO_RUN_QUEUE=db` makes queued runs survive a restart, but a second API process would still double-start cycles and see different live state. This is written down in "Trade-offs and known limits" and is acceptable for the stated audience. It is listed here because R1 to R3 and R7 are the same decision: whether the team server is ever meant to scale past one box.

### R8. Plugin API has no version (Low)

`FrameworkAdapter` and `BaseReporter` are the public extension points, loaded by entry points. Neither carries an API version, and `qasight plugins` cannot tell a plugin built for 1.3 from one built for 1.5. Adding a method to `FrameworkAdapter` (as `native_report` already was) breaks third-party adapters at call time, not load time.

### R9. Concurrency model for parallel stages is per-process (Low)

With `max_parallel_stages`, several stages append to one `events.ndjson` from threads in one process. That is safe with the GIL and line-sized writes, but the format has no sequence number, so a consumer cannot order or de-duplicate events once a run is spread over the container runner service or two processes. A `seq` field on every event is cheap now and impossible to retrofit later.

### R10. Documentation drift (Low)

Besides R5: the layer table still says `run_plan()` runs stages "in order" (parallel stages exist), lists `services/` as analytics only, and the test layout note (`tests/TEST_LAYOUT.md`) describes a migration that finished long ago and a `tests/contracts/` folder with one file next to `tests/contract/` with 26. Small, but the vault's rule is "docs stay in sync", and these are the notes an outside contributor reads first.

## What is good and should not change

- **One engine, one use case.** `CycleRunService` is still the only caller of `run_plan()`; the CLI, the API, ingest, `pytest --qasight` and the Action all go through it.
- **Events as the seam.** Typed events, NDJSON on disk, renderers decide presentation. The container runner backend slotted in under the executor without touching the engine's contract, which is the proof the seam is in the right place.
- **Backends behind a protocol** (`engine/backends/`, `StageProcess`), so the host and container runners are interchangeable and the API never sees a Docker socket.
- **Generated frontend types** with a CI gate on drift. 85 Pydantic models in one `qasight_api/models.py` is big but it is one place, and that is the point.
- **Switchable team features** default off (`TESTO_AUTH`, `TESTO_RUN_QUEUE`, `TESTO_RUNNER`, `TESTO_REPORTS_ORIGIN`): a solo user's install stays simple.
- **Exit codes single-sourced** and locked by contract tests.

## Recommendations

Ordered by value for effort. The first three are one decision in three steps; the rest are independent.

### 1. Add migrations before anything else touches the schema

**Decision:** introduce Alembic with a baseline revision equal to today's `create_all()` schema; `qasight-api` and `qasight config-db` run `upgrade head` on startup, SQLite included. Keep `create_all()` only for the in-memory test database.

| Option | Complexity | Risk | Notes |
|--------|-----------|------|-------|
| A. Alembic, autogenerate from SQLModel metadata | Low | Low | Standard, one dependency already pulled in by SQLAlchemy's ecosystem. Recommended. |
| B. Hand-rolled `schema_version` table plus `ALTER` scripts | Low | Medium | Reinvents Alembic, three dialects to test by hand. |
| C. Keep `create_all()`, document "drop and recreate" | None | High | Unacceptable once the team server has a Postgres with months of history. |

**Consequences:** every later schema change (2, 3 below) is a revision; the docker-compose team server upgrades in place; the Repository Pattern ADR gets a section on migrations. About one PR.

### 2. Promote the hot columns out of the JSON blob

**Decision:** add real columns to `RunRecord` for `cycle`, `health_pct`, `exit_code`, `branch`, `commit`, `source` and `snapshot_dir`, with indexes on `(cycle, start_time)` and `start_time`. Keep `metadata` for everything else. Add `list_runs(cycle=..., since=..., limit=...)` to the repository protocol and move the eight Python-side cycle filters onto it.

| Option | Complexity | Scalability | Notes |
|--------|-----------|-------------|-------|
| A. Columns + indexes, blob stays for the long tail | Medium | Good | Dialect-portable, SQL-queryable from Grafana. Recommended. |
| B. Dialect JSON indexes (JSONB GIN on Postgres, `json_extract` on SQLite) | Medium | Medium | Three dialects, three syntaxes, MySQL weakest; the blob stays the contract. |
| C. Leave as is, raise `_SCAN_LIMIT` | None | Poor | Pushes the cliff back, keeps O(N) decoding per request. |

**Consequences:** `_SCAN_LIMIT` disappears; the dashboard and cycle pages stop reading JSON they do not need; `history/views.py` becomes a thin mapper. Needs 1. One or two PRs.

### 3. Give tests a table

**Decision:** on each run, write one row per test case (`run_id`, `stage`, `key`, `status`, `duration_ms`, `message` truncated) into a `test_result` table from the same Allure JSON the snapshot already parses. Flaky, per-test history, "got slower", triage and `/health` read the table; the files stay for attachments, traces and the Compare download.

| Option | Complexity | Scalability | Notes |
|--------|-----------|-------------|-------|
| A. `test_result` table written by `DbBackend`, files kept for artifacts | Medium-High | Good | The API no longer needs the run's disk for analytics; retention becomes a `DELETE`. Recommended. |
| B. Cache parsed snapshots in memory per process | Low | Medium | Helps latency, not the disk coupling or the 500-run ceiling. |
| C. Move snapshots to object storage, keep file reads | High | Medium | Solves disk sharing, keeps O(runs × tests) parsing. |

**Consequences:** the biggest change in this note and the one that makes the team server honest about scale. Each analytics module shrinks (they already share `_load_cases`). Suites with tens of thousands of tests per run produce large tables; `message` must be bounded and old rows pruned (see 6). Two or three PRs, behind nothing (it is a write-side addition; reads can switch one module at a time). Needs 1 and 2.

### 4. Enforce the layer table with `import-linter`

Add an `importlinter` config to `pyproject.toml` with three contracts: `qasight` must not import `qasight_api` except `cli/commands/ui_cmd.py`; `engine`, `config`, `frameworks`, `paths` must not import `services`, `reporting`, `cli`, `resources`; `persistence` and `history` may import `repository` but not each other. Run it in the `format` CI job. Move `safe_child_path` and `plan_artifacts_dir` from `reporting/paths.py` into `qasight/paths.py` to make the first contract pass. Half a day, and it stops R6 from growing.

### 5. Split `services/` by role

Not a rewrite: three moves and a docstring. `services/cycle_run.py` is the use case and stays. Analytics (`delta_*`, `flaky_service`, `test_history`, `dashboard_service`, `attention`, `run_failures`, `run_snapshot_diff`, `project_health`) become `qasight/analytics/`. Exporters (`otel_export`, `run_summary_markdown`, `badge`, `report_archive*`) join `reporting/`. Write paths (`junit_ingest`, `triage`, `run_annotations`, `test_ownership`) stay in `services/`. `services/ai/` stays. Then the layer table in `ARCHITECTURE.md` can name each package in one line. One mechanical PR per move; `import-linter` from 4 keeps them apart afterwards.

### 6. Retention for `static/history/`

A `history.retention` config key (days and/or runs per cycle) and a `qasight clean --history` command that deletes the oldest rows and their `static/history/<run_id>/` directories together, run by the API on a timer when set. Without it the team server's disk is the real `_SCAN_LIMIT`. Small, independent of 1 to 3.

### 7. Version the plugin API

Add `TESTO_PLUGIN_API = 1` in `qasight/plugins.py`, let an adapter or reporter class declare `plugin_api = 1`, and have `qasight plugins` and `load_framework()` warn on a mismatch. Document in [Plugin API](Plugin%20API%20-%202026-10-19.md) which protocol methods are optional. One small PR; it makes `native_report`-style additions safe.

### 8. Add `seq` to engine events

An integer sequence per run in `engine/events.py`, written to `events.ndjson` and forwarded by SSE and the container runner. The frontend can then de-duplicate reconnects and the OTel exporter can order spans without the wall clock. Tiny, and only cheap now.

### 9. Fix the doc drift in one PR

`ARCHITECTURE.md`: the session rule (R5), `services/` description (R4), "in order" (R10), and a "Scaling" subsection that says plainly: one API process, shared disk, SQLite or Postgres, and what 1 to 3 would change. Retire `tests/TEST_LAYOUT.md`'s migration map into `docs/Archive/` and fold the tier list into `tests/README.md`. Half a day.

### Not recommended

- **Microservices or a message broker.** The in-process worker pool plus `TESTO_RUN_QUEUE=db` is the right size; the container runner service is already the one process boundary that security needs.
- **Replacing SQLModel.** The three-dialect story works; the problem is the shape of one table, not the ORM.
- **A second UI or a plugin UI slot.** Not asked for, and the generated-types gate is worth more than extensibility here.

## Sequence

1. Migrations (rec. 1), `import-linter` and path helper move (rec. 4), doc drift (rec. 9), event `seq` (rec. 8). All small, no behaviour change.
2. Hot columns and the repository query (rec. 2), retention (rec. 6).
3. `test_result` table and switching analytics onto it (rec. 3), then the `services/` split (rec. 5) once the analytics modules have shrunk.
4. Plugin API version (rec. 7) in the release after, so plugin authors get one notice.

Nothing above changes `qasight.yaml`, the CLI contract or exit codes. Recommendations 2 and 3 change the database schema and need 1 first.

## Related

- [Architecture Consolidation - 2026-10-06](Architecture%20Consolidation%20-%202026-10-06.md): the decisions this review builds on.
- [Repository Pattern - Database-Agnostic Refactor](Repository%20Pattern%20-%20Database-Agnostic%20Refactor.md): where recommendations 1 to 3 would be recorded once decided.
- [Scale Runs and Stages - 2026-10-24](Scale%20Runs%20and%20Stages%20-%202026-10-24.md) and [Switchable Team Features - 2026-10-24](Switchable%20Team%20Features%20-%202026-10-24.md): the write-side scaling work already done.
- [Throwaway Stage Containers - 2026-10-24](Throwaway%20Stage%20Containers%20-%202026-10-24.md): the backend seam praised above.
- [Technical Debt Tracker](../Testing%20Workflows/Technical%20Debt%20Tracker.md): the open items R1 to R10 should be mirrored into once Taltal picks which to do.
