---
type: spec
status: current
created: 2026-10-22
updated: 2026-11-04
related: "Command Adapter and JUnit Import, Architecture Consolidation, Team Server"
---

# Ingest and Team Server - 2026-10-22

## Context

A review of what keeps QASight niche (2026-10-22) found that the tools teams actually adopt (ReportPortal, Currents, Trunk, Testmo) ask for one line in an existing CI job and give back one place the whole team looks at. QASight asked users to move their test commands into `qasight.yaml`, and kept history in a SQLite file on one machine. In CI that file disappears with the runner. Two features close the gap: record results that some other step produced, and a server many CI jobs can record into.

## Decision

1. **`equipment: junit`** (`qasight/frameworks/junit_adapter.py`). An adapter with `runs_process = False`. The executor (`_record_junit_stage`) skips the subprocess, imports the stage's `junit_xml` reports with the existing converter (`reporting/junit_import.py`), without the "newer than the stage" filter, and derives the returncode: 1 when a case failed or broke, or when nothing matched; 0 otherwise. Stage duration is the summed test time, and the plan's duration is the larger of the wall clock and the summed stage durations, so ingested runs have meaningful duration trends.
2. **`qasight ingest`** builds a one-stage `junit` cycle (`cycle_run.junit_ingest_plan`, strict name and pattern checks) and runs it through `CycleRunService`, like `qasight run`. Exit codes follow the engine contract. No `qasight.yaml` is needed (`loader.load_config_or_defaults`).
3. **`POST /api/v1/ingest`** (`qasight_api/routes/ingest.py` → `services/junit_ingest.py`) writes the uploaded files to a scratch directory and records them through the same service, one upload at a time (a process lock). The client's CI provenance is sent as form fields, because the server's own environment says nothing about the client's CI; it reaches the run record through a new `CycleRunOptions.run_metadata` → `run_plan(run_metadata=)` → `PlanResult.extra["run_metadata"]`, merged last by the DB backend. Uploads are size-limited and never executed.
4. **`qasight ingest --server URL --token`** (`services/ingest_client.py`) uploads instead of recording locally, and exits with the server's result, or 3 when the upload fails.
5. **The API serves the UI** when `TESTO_UI_DIR` points at a build (`qasight_api/ui.py`, SPA fallback after all API routes). Same-origin writes are allowed only when `TESTO_API_TOKEN` is set, because a DNS-rebinding page is also "same origin".
6. **`Dockerfile.qasight-server`** (UI build + API with `[api,db]`) and **`deploy/server/docker-compose.yml`** (plus Postgres, token required). `docker-publish.yml` publishes `qasight-server` next to `qasight-runner`; the `team_server` CI job runs the compose file end to end.

7. **Lighter install** (the third item of the same review). Behave, BehaveX and Allure-Behave move from the core dependencies to a `bdd` extra, so `pip install qasight` for `qasight ingest` or pytest/Playwright cycles pulls 35 packages instead of 47. The `dev` extra and the `qasight-runner` image include `bdd`. A `behave`/`behavex` stage without it fails with rc 127 (exit 3), and the stage error, now shown in the CLI stage panel, names the extra. `scripts/package_smoke.sh` checks that a plain install has neither. The GitHub Action's `package` input and a new `TESTO_PACKAGE` variable in the GitLab template select the extra. The README and PyPI description now lead with what a run answers (what broke, what's flaky, what got slower) and with `qasight ingest`.

## Options considered

- **A separate ingest pipeline that writes run records directly.** Rejected: a second path into history is what the [Architecture Consolidation](Architecture%20Consolidation%20-%202026-10-06.md) removed. Going through `CycleRunService` gives ingested runs reporters, snapshots, the report archive and metrics push for free.
- **Upload Allure results instead of JUnit.** Deferred. Almost every runner writes JUnit XML; native formats (Playwright JSON) can be added as more `runs_process = False` parsers.
- **Accounts and projects on the server.** Deferred. One token is enough to try it in a team, and it matches the existing `TESTO_API_TOKEN` model.
- **Ship the UI inside the PyPI wheel (`qasight ui`).** Still open. The server image covers the team case without growing the wheel.

## Consequences

- `SUPPORTED_FRAMEWORKS` gains `junit`. The ad-hoc run API doesn't offer it (it needs files, not a command).
- New CLI command, API route, env var (`TESTO_UI_DIR`), Docker image and CI job; documented in [Command Reference § qasight ingest](../CLI%20Commands/Command%20Reference.md#qasight-ingest), [Team Server](../Processes%20&%20Guides/Team%20Server.md), [Publishing Docker Images](../Processes%20&%20Guides/Publishing%20Docker%20Images.md), [Architecture Overview](../Architecture/Architecture%20Overview.md) and [Deep Dive - Execution Logic](../Architecture/Deep%20Dive%20-%20Execution%20Logic.md).
- Tests: `tests/unit/qasight/test_junit_ingest.py` (config, plan validation, executor, run metadata, CLI) and `tests/contract/api/test_ingest_contract.py` (endpoint, token, limits, UI serving, same-origin rule, upload client).
- The first `qasight-server` push creates a private GHCR package that the owner makes public once.
- Uploaded-only cycles don't appear on the Cycles page (it lists `qasight.yaml`). Follow-up.
