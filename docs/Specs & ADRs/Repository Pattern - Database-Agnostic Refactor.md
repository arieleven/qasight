---
type: spec
status: current
created: 2026-06-25
updated: 2026-11-05
---

# Repository Pattern — Database-Agnostic Refactor

<!-- source: notion https://www.notion.so/354d95cd031280a0b949f5d011bab26b -->

## Decision (WHY)

Teams should adopt QASight without migrating to a mandated database. Run history and archives must work against SQLite (zero-config local), PostgreSQL (compose/default), or MySQL (enterprise) via a single connection string.

## Current implementation (HOW — codebase)

| Piece | Location |
|-------|----------|
| URL resolution | `qasight/repository/db_config.py` — `DATABASE_URL`, legacy `POSTGRES_*`, default SQLite |
| Service locator | `qasight/repository/db.py` — `get_repository()`, `get_report_archive_repository()` |
| Protocol | `qasight/repository/base.py` — `BaseRunRepository` |
| Adapters | `qasight/repository/adapters.py`, `factory.py` |
| Models | `qasight/repository/models.py` — `RunRecord` with its promoted, indexed columns plus a JSON `metadata` blob for the long tail |
| Schema migrations | `qasight/repository/migrations/` — Alembic revisions, run in-process by `create_db_and_tables()` |

Configure via `database.url` in [qasight.yaml](../../qasight.yaml) or `DATABASE_URL`. See [Command Reference § Environment variables (common)](../CLI%20Commands/Command%20Reference.md#environment-variables-common) and [Architecture Overview § Configuration as the single source of truth](../Architecture/Architecture%20Overview.md#configuration-as-the-single-source-of-truth).

## Schema migrations (2026-11-01)

Until 1.5.0 the schema came from `SQLModel.metadata.create_all()` at startup, and a column added to an existing table needed a manual `ALTER TABLE` (or each store patched its own table at startup, as `resources/store.py` did). Since [Architecture Review - 2026-10-31](Architecture%20Review%20-%202026-10-31.md) recommendation 1, the schema is owned by Alembic revisions in `qasight/repository/migrations/versions/`, numbered `0001_baseline.py`, `0002_…`:

- `create_db_and_tables()` (`repository/db_config.py`) upgrades the active database to the newest revision. Every store (`accounts.py`, `services/triage.py`, `resources/store.py`, `qasight_api/run_queue.py`) and the repository locator call it; nothing calls `create_all()` on a real database any more. Several processes starting on one database at once is fine: the first upgrades, the rest find the schema at head.
- A database from 1.5.0 or earlier (tables but no `alembic_version`) is adopted on first open: missing tables and columns are added with the models' defaults, then it is stamped at `0001` and upgraded. Nothing is dropped and nobody runs a command by hand; the team server's Postgres upgrades in place when the image restarts.
- A model change ships with a revision in the same pull request. `python scripts/db_revision.py "what changed"` writes it from the model diff (read it: autogenerate cannot see renames). `tests/unit/qasight/repository/test_migrations.py` compares the models with the migrated schema and fails on any difference.
- `alembic` is a core dependency, like `sqlmodel`, because `qasight run` always saves history.

## Promoted run columns (2026-11-01)

`RunRecord` kept everything but `id`, `status` and the two timestamps inside its JSON `metadata`, so listing one cycle's runs meant loading the newest 500 rows and filtering in Python, and five analytics modules carried a `_SCAN_LIMIT = 500` ceiling. Recommendation 2 of the [Architecture Review](Architecture%20Review%20-%202026-10-31.md), revision `0002`:

- Real columns, filled on every write from the JSON by `models.promoted_columns()` with the same precedence the views use: `cycle` (from `plan`), `health_pct`, `exit_code`, `branch` (`ci_ref_name`, else `git_branch`), `commit_sha` (`ci_commit_sha`, else `git_commit`), `source`, `snapshot_dir`. Indexes on `start_time`, `cycle` and `(cycle, start_time)`. The migration backfills existing rows; the JSON stays the full record and the views fall back to it.
- Repository queries that use them: `list_runs(cycle=, before=, limit=)` and `latest_run_per_cycle()` (one `GROUP BY`, every cycle). The read side exposes them as `history.read_model.list_cycle_runs(cycle, limit=, before_run_id=)` and `latest_run_per_cycle()`; flaky detection, per-test history, failures, project health, the PR comment, attention and OTel export all read through them. `CYCLE_HISTORY_LIMIT` (500) bounds one cycle's history, not the whole table, so a busy cycle no longer hides a quiet one.
- Per-test data (`flaky`, per-test page, "got slower") was still read from each run's artifact snapshot on disk; the `test_result` table below replaced that.

## Per-test table (2026-11-01)

Every analytic that looked at single tests (`flaky`, per-test history and the "got slower" list, run failures, the PR comment, run alerts) opened each run's Allure snapshot under `static/history/<run_id>/artifacts/` and parsed the `*-result.json` files again, with its own copy of the parser. Recommendation 3 of the [Architecture Review](Architecture%20Review%20-%202026-10-31.md), revision `0003`:

- `TestResult` (`test_result`): one row per test per run, keyed by `(run_id, key)` where `key` is the Allure `historyId` (else the full name). Columns: `name`, `full_name`, `group`, `stage`, `framework`, `status`, `duration_ms`, `message` (first 300 characters). Indexed on `run_id`. The row is the case map every service already used, so the services did not change shape.
- One parser: `qasight/reporting/allure_cases.py` (`load_cases(plan_root)`), used by the writer, the lazy adoption and the snapshot diff.
- Writer: `persistence/db_backend.py` stores the rows right after the run row and its snapshot (`TestResultStore.replace`); a failure there is logged and the run keeps its snapshot.
- Reader: `history/cases.py::cases_for_run(run)` returns the rows, and for a run recorded before `0003` parses its snapshot once and stores the rows, so older history migrates itself on first use and never needs its files again for analytics. `TestResultStore` (`repository/test_results.py`) is the only module that touches the table; `TestResultStore.delete_runs()` is for retention.
- Snapshots stay for the things rows cannot hold: attachments, traces and the report copies served at `/history`.

## History retention (2026-11-01)

Nothing ever deleted a run: `qasight clean` removed `artifacts/`, not `static/history/`, and the table only grew (review finding R1, recommendation 6). Now:

- `history.retention` in `qasight.yaml` (`days`, `runs_per_cycle`, either or both; `config/history_section.py`) says when a run expires. No section, no deletion.
- `history/retention.py::prune_history()` selects the expired runs from the promoted columns alone (`repository.list_run_keys()`: id, cycle, start time, status; `RUNNING` runs never expire), then deletes their `test_result` rows, their `runrecord` rows (`repository.delete_runs()`, the one delete on the run table) and their `static/history/<run_id>/` folders, in that order, so a run never outlives its files. Report archives are a separate store and are not touched.
- Callers: `qasight clean --history` (with `--dry-run`, `--keep-days`, `--keep-runs`) and the API, which prunes at startup and every 6 hours on a daemon thread when the section is set (`qasight_api/main.py`).

## Release verification

[Release Checklist - Phase 1 Foundation](../Archive/Release%20Checklist%20-%20Phase%201%20Foundation.md) — repository contract tests and packaging gate.

---
**Context & Links:** [Architecture Overview](../Architecture/Architecture%20Overview.md), [Product Roadmap § Phase 1: Decoupling & Distribution (Foundation)](../Roadmap%20&%20Strategy/Product%20Roadmap.md#how-it-got-here), [Release Checklist - Phase 1 Foundation](../Archive/Release%20Checklist%20-%20Phase%201%20Foundation.md)
