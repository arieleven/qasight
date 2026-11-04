# QASight — Architecture

QASight (`qasight`) runs multi-stage test **cycles** defined in `qasight.yaml`
(pytest, Behave, BehaveX, or any command that writes JUnit XML), stores every run, and
turns the results into reports, dashboards, run-to-run deltas and AI failure summaries.

There is **one execution engine**. The CLI, the REST API and the React UI are thin
adapters over the same use case, `CycleRunService`, which drives the engine. Stages run as
host subprocesses; Docker is only used to host an optional Postgres and to package the CLI
as a runner image.

## The big picture

```mermaid
flowchart TD
    YAML[qasight.yaml] --> CFG[config: discover_and_load / resolve_plan]

    CLI["qasight run (Typer CLI)"] --> SVC
    API["FastAPI /api/v1<br/>cycles + adhoc executions"] --> MGR[CycleExecutionManager<br/>background thread per execution]
    MGR --> SVC
    UI[React frontend] -->|HTTP + SSE| API
    CI[GitHub Action / GitLab template] -->|"qasight run --ci"| CLI

    CFG --> SVC[CycleRunService<br/>trigger gate → engine → reporters → metrics → archive]
    SVC --> ENG[engine.run_plan<br/>sequential stages]
    ENG --> EXE[executor.run_stage<br/>subprocess + timeout]
    EXE --> FW[framework adapters<br/>pytest · behave · behavex · playwright · command]
    ENG --> EV[(artifacts/&lt;cycle&gt;/events.ndjson)]
    ENG --> PER[persistence<br/>JsonBackend + DbBackend]
    PER --> DB[(Run history DB<br/>SQLite / Postgres / MySQL)]
    SVC --> REP[reporters<br/>Allure · Extent · ReportPortal · TestBeats]
    SVC --> ARC[(Report archive DB)]
    MGR -. tails .-> EV
```

## Layers

| Layer | Package | Responsibility |
|-------|---------|----------------|
| Configuration | `qasight/config/` | Discover and parse `qasight.yaml`; immutable `QASightConfig` / `Plan` (cycle) / `Stage` dataclasses; `${env:…}` interpolation and `if:` filtering; `triggers.py` decides whether a cycle's `trigger:` paths changed. |
| Use case | `qasight/services/cycle_run.py` | `CycleRunService.run()`: trigger gate, engine call, configured reporters, native-report snapshot, metrics push, report archive. `single_stage_plan()` wraps one framework call as a one-stage `adhoc` cycle. |
| Engine | `qasight/engine/` | `run_plan()` runs stages in order and emits typed events; `run_stage()` spawns the subprocess, tees `run.log`, enforces timeouts; `exit_codes.py` is the single exit-code taxonomy. |
| Framework adapters | `qasight/frameworks/` | Build argv and Allure output dirs per framework. `command` runs any argv and imports its JUnit XML as Allure results. |
| Persistence | `qasight/persistence/` | Best-effort backends behind one protocol: `plan_result.json` and a `RunRecord` row with health %, per-stage counts, failure evidence and CI provenance. |
| Storage | `qasight/repository/` | Dialect-agnostic repository (SQLite default, Postgres/MySQL via `DATABASE_URL`); `db_config.py` resolves the URL and builds the engine, `db.py` caches the process-wide repository. The only code that opens a database session. |
| Run history | `qasight/history/` | Read side over stored runs: typed views, queries, report links and snapshot files. Reads through the repository only. |
| Reporting | `qasight/reporting/` | Collect Allure results from the artifacts tree; generate Allure / Extent / ReportPortal / TestBeats output; `qasight report` commands; run KPIs (`metrics.py`, `metrics_extractor.py`) and their InfluxDB / Pushgateway push (`integrations.py`). |
| Analytics | `qasight/services/` | Dashboard rollups, run-to-run delta, AI failure analysis (bring-your-own-key providers in `services/ai/`). |
| Adapters | `qasight/cli/`, `qasight_api/`, `frontend/` | Presentation only: CLI renderers (Rich / NDJSON), FastAPI routes + SSE, React pages. |

`qasight/paths.py` is the only module left at the package root: it defines the
`artifacts/` and `static/` layout that every layer above shares, including
`safe_child_path()` and `plan_artifacts_dir()`, the two helpers every layer uses to
address a cycle's artifacts.

CI keeps this table honest: `lint-imports` (import-linter, contracts in
`pyproject.toml` under `[tool.importlinter]`, run by the `format` job) fails when
`qasight` imports `qasight_api`, when anything below the analytics imports
`services/` or `cli/`, when `repository/` imports a layer above it, when `config/`,
`frameworks/` or `paths.py` import the engine or the read/write sides, or when
`persistence/` and `history/` import each other. The few known exceptions are listed
next to their contract as `ignore_imports`: `qasight ui` starting the API server,
`persistence/db_backend.py` reading CI provenance from `services/`, and the plugin
loader knowing the reporter base class.

## One run, end to end

1. **Entry.** `qasight run --cycle smoke`, `POST /api/v1/cycles/smoke/executions`, or
   `POST /api/v1/adhoc-executions` (one framework, no cycle in YAML).
2. **Plan.** The config is loaded and the cycle resolved (or `single_stage_plan()` builds an
   `adhoc` plan). Stages whose `if:` is false are dropped.
3. **Trigger gate.** If the cycle has a `trigger:` block and `--force` is not set, changed
   paths are checked; a resting cycle exits `0` without running anything.
4. **Engine.** `run_plan()` runs each stage as a subprocess in its `target_repo`, writing
   `artifacts/<cycle>/<stage>/run.log` and Allure results, and appends every event to
   `artifacts/<cycle>/events.ndjson`. The CLI renders events live (Rich panels or NDJSON with
   `--ci`); the API tails `events.ndjson` and forwards each line as an SSE message.
   The API keeps running executions plus the last `TESTO_MAX_FINISHED_EXECUTIONS` (default
   200) finished ones in memory; older ones are only in the run history. At most
   `TESTO_MAX_CONCURRENT_RUNS` (default 2) execute at once; the rest wait in a FIFO queue
   as `queued`, in memory or, with `TESTO_RUN_QUEUE=db`, also in the database so they
   survive a restart (`qasight_api/run_queue.py`). Stages run in order unless a cycle sets `max_parallel_stages`.
5. **Persist.** `JsonBackend` writes `plan_result.json`; `DbBackend` writes a `RunRecord`
   (status, durations, per-stage health, failed cases + traceback + log tail when the run
   failed, CI provider/commit/ref when run in CI). Failures here never fail the run.
6. **Post-run.** Configured reporters write per-run HTML under `static/history/<run_id>/`
   (served by the API at `/history`), native reports are copied next to them, KPIs are pushed
   to InfluxDB / a Prometheus Pushgateway when those are configured, and the cycle's report
   bundle is archived to the report DB (`qasight report list/open/diff`).
7. **Read side.** The dashboard, Runs, Run Detail, Compare and AI summary endpoints all read
   the run history through `qasight/history/` and the services on top of it.

## Interfaces

### CLI (`qasight`)

`qasight run`, `qasight report …`, `qasight cycles …`, `qasight diff`, `qasight summary`,
`qasight config …`, `qasight config-db`, `qasight init`, `qasight watch`, `qasight doctor`, `qasight clean`. Full reference: `docs/CLI Commands/Command Reference.md`.

Exit codes (`qasight/engine/exit_codes.py`), propagated unchanged to CI:

| Code | Meaning |
|------|---------|
| `0` | Success, including a trigger-skipped cycle |
| `1` | Tests failed |
| `2` | Invalid config or CLI input |
| `3` | Infrastructure failure (timeout, missing executable, required archive failed) |
| `4` | Internal engine error |

### REST API (`qasight_api/`, `/api/v1`)

| Area | Endpoints |
|------|-----------|
| Cycles | `GET /cycles`, `GET /cycles/{cycle}` |
| Executions | `POST /cycles/{cycle}/executions`, `POST /adhoc-executions`, `GET /cycle-executions/{id}`, `GET /cycle-executions/{id}/events` (SSE) |
| Runs | `GET /runs`, `GET /runs/{run_id}`, `GET /runs/{run_id}/reports`, `GET /runs/{run_id}/pyramid` |
| Analytics | `GET /analytics/delta`, `GET /analytics/delta/cases`, `GET /analytics/flaky`, `GET /dashboard/overview`, `GET /dashboard/runs/recent` |
| AI | `GET /ai/config/status`, `PUT /ai/config`, `GET /runs/{run_id}/ai-summary`, `POST /runs/{run_id}/ai-summary:generate` |
| Health | `GET /health/live`, `GET /health/ready` |

Named-cycle and ad-hoc executions are the same resource: both return a
`/cycle-executions/{id}` status URL and event stream, and both run through `CycleRunService`.
Only one execution per cycle name (`adhoc` included) runs at a time; a second request gets `409`.
Access control is deliberately small; see [Security model](#security-model).

### Frontend (`frontend/`, Vite + React + Tailwind)

Dashboard (`/`), Cycles (`/cycles`, `/cycles/:name` with a live run panel), Runs (`/runs`,
`/runs/:runId`), Compare (`/compare`), Quick Run (`/quick-run`, one framework without a
cycle) and AI settings (`/settings/ai`). Pages only call typed `/api/v1` contracts; live
progress for both run panels comes from one hook, `features/execution/useCycleExecution.ts`.

### CI wrappers

`action.yml` at the root (composite action, listed on the Marketplace; its script lives in `integrations/github-action/`) and `ci/gitlab/testo.gitlab-ci.yml` both run
`qasight run --ci` and keep the NDJSON stream plus the final `plan_finished` event as artifacts.
`Dockerfile.qasight-runner` packages the CLI as a runner image (`ENTRYPOINT ["qasight"]`).

## Artifact layout

```text
artifacts/<cycle>/
  events.ndjson               # every engine event, append-only (tailed by the API)
  plan_result.json            # JsonBackend output
  <stage>/
    run.log
    allure-results/<framework>/*-result.json
static/history/<run_id>/      # per-run reporter output, served at /history
```

## Storage

- **Run history** (`RunRecord`, one row per cycle execution, written by `DbBackend`) is what
  every UI page and the delta/AI services read.
- **Report archives** (`ReportArchive`) are zipped report bundles keyed by their own UUID,
  written by `CycleRunService` after each run for `qasight report list/open/diff`. They are not
  linked to a run id.
- Run artifacts and HTML reports stay on local disk (`static/history/<run_id>/`), served by
  the API. The default database is SQLite; `docker-compose.yml` only provides Postgres for
  team setups.

## Design decisions

- **One engine, many adapters.** Every way of starting a run (CLI, API cycle, API ad-hoc,
  CI wrappers) goes through `CycleRunService`, so behaviour such as trigger gating, reporters
  and archiving cannot drift between surfaces. Until v1.1 a second, Docker-based stack
  (`HeadlessEngineService` → `runners.py`) and a Streamlit UI existed beside it; they were
  removed and their unique features (CI provenance, failure context, metrics push, ad-hoc
  runs) moved onto the engine.
- **One typed contract from Pydantic to React.** `qasight_api/models.py` is the only place the
  HTTP contract is written. `scripts/export_openapi.py` exports FastAPI's OpenAPI schema to
  `frontend/openapi.json`, and `openapi-typescript` generates `frontend/src/lib/api-schema.ts`
  from it. CI fails if either file is stale or `tsc` finds a mismatch, so a backend change
  that breaks the UI fails the build instead of the browser.
- **Events as the integration seam.** The engine emits typed events; renderers decide
  presentation. `events.ndjson` doubles as the durable log the API streams from, so the API
  never holds run output in memory and the same file is available after the run.
- **Best-effort side effects.** Persistence, reporters, metrics pushes and archiving never
  change a run's exit code, except a required report archive under `--ci` (exit `3`).
- **Config is the source of truth.** Cycles, defaults, reporters and the database URL live in
  `qasight.yaml`; the UI lists and runs what the file defines rather than keeping its own copy.

## Security model

QASight is a **local developer tool**, and the API is a command runner by design:
`POST /adhoc-executions` takes `framework: command`, any `target_repo`, `args` and `extra_env`.
The defaults assume one trusted user on one machine:

- `qasight-api` binds `127.0.0.1` (`TESTO_API_HOST`). Binding anything else without a token
  refuses to start (exit 2) unless `TESTO_ALLOW_UNAUTHENTICATED=1` says the network is
  already trusted; then it logs a warning.
- `TESTO_API_TOKEN`, when set, requires `Authorization: Bearer <token>` or the session cookie
  from `/login` on every mutating request (constant-time compare, `401` otherwise). The
  cookie is HttpOnly, `SameSite=Strict` and an HMAC of the token, never the token itself.
  `TESTO_PROTECT_READS=1` extends that to every request, the `/history` report files and the
  UI included (a middleware, since those are static mounts); browsers asking for a page are
  sent to `/login`, and only health checks stay open. The team server turns it on. Otherwise
  reads stay open, so the `?config_path=`
  that read routes accept only loads files named `qasight.yaml`, `qasight.yml` or
  `pyproject.toml` (`400` otherwise); an arbitrary path would let anyone probe the server's files. The frontend sends
  `localStorage["testo.apiToken"]` or the build-time `VITE_TESTO_API_TOKEN`, which ends up in
  the JS bundle, so only bake it into a UI served to the people who hold the token anyway.
- CORS allows only the Vite dev/preview servers unless `TESTO_CORS_ORIGINS` lists origins;
  credentials are allowed only for an explicit list, never with `*`. CORS alone does not
  stop a cross-site "simple" POST, so mutating requests whose `Origin` is not on that list
  get `403` even without a token. That is what keeps a random web page (or a DNS-rebinding
  one) in the developer's browser from starting runs on the localhost API.
- Every response carries `X-Content-Type-Options: nosniff` and `frame-ancestors 'self'`. Report
  files under `/history` share the API's origin, so report attachments (Allure's
  `*-attachment.*` and `data/attachments/`, which include uploaded Playwright files) are served
  with `Content-Security-Policy: sandbox`: an HTML attachment still displays but cannot run
  script with the viewer's session. Report pages themselves are not sandboxed, since they need
  script and their own origin to load their data. `TESTO_REPORTS_ORIGIN` (off by default) gives
  them that origin: a second hostname for the same server that serves only `/history`. The API
  host redirects report links there with a 60-second signed ticket, the reports host trades it
  for its own HttpOnly cookie that can only read reports, and the API's Origin check refuses any
  write a report page attempts (`qasight_api/report_origin.py`).
- Sessions are stateless: the `/login` cookie is an HMAC of the token, so signing out only
  clears that browser's copy. A copied cookie keeps working until `TESTO_API_TOKEN` changes,
  so rotate the token to sign everyone out.
- The AI provider's `base_url` is free-form, so anyone with a write token can point it at any
  host and the provider key is sent there. Treat the write token as able to read that key.

- `TESTO_AUTH=accounts` (off by default) replaces the shared token for people: each person
  signs in at `/login` with a username and password (`scrypt`-hashed, managed with
  `qasight users`, no sign-up page). Roles are `viewer` (reads), `runner` (also starts runs and
  uploads) and `admin` (also AI settings and the audit log at `GET /api/v1/audit`); every
  write and sign-in attempt is recorded with its user. Reads are always protected in this
  mode. The token stays an admin credential for CI and signs the session cookies, which carry
  the account's session version, so a role or password change signs that person out
  everywhere. After `TESTO_LOGIN_MAX_FAILURES` (default 5) failed sign-ins for one name in 15
  minutes, or four times that from one address, further attempts get `429` without a password
  check until the window slides (`qasight_api/login_throttle.py`, per process).
  See [Switchable Team Features](docs/Specs%20&%20ADRs/Switchable%20Team%20Features%20-%202026-10-24.md).

Accounts give a team per-user access and an audit trail, but by default a `runner` can still
run any command (`framework: command`, free-form `args`/`extra_env`) as the API's own user.
`TESTO_RUNNER=container` (off by default) closes that: the executor hands each stage to the
runner service (`qasight/engine/backends/`), a separate container that alone holds the
Docker socket and starts the stage with `docker run --rm` in a fresh container. The service,
not the request, decides the image, the mounts (the stage's directory must be inside one), the
commands allowed and the limits: no Linux capabilities, `no-new-privileges`, a process limit,
optional memory, CPU and network limits. Only the stage's own variables travel with it, never
the server's environment, so a test can't read `TESTO_API_TOKEN` or `DATABASE_URL`. A timeout,
or the API going away, stops the container. See
[Throwaway Stage Containers](docs/Specs%20&%20ADRs/Throwaway%20Stage%20Containers%20-%202026-10-24.md).

## Trade-offs and known limits

- **Stages run one at a time by default.** A cycle is an ordered list (`max_parallel_stages`
  opts in to running several at once); parallelism otherwise lives inside a stage
  and is per framework: `workers` becomes BehaveX `--parallel-processes`, while pytest
  stages pass xdist's `-n` in `args` and native Behave and `command` stages ignore it.
- **One execution per cycle at a time.** A second request for a running cycle gets `409`
  rather than queueing; artifacts are per cycle, so two runs would interleave `events.ndjson`.
- **In-memory execution registry.** `CycleExecutionManager` keeps execution state in the API
  process, so it is single-process (no multiple uvicorn workers) and a restart forgets live
  executions; finished runs survive in the database, and orphaned `RUNNING` rows are marked
  failed on startup.
- **SSE is a file tail.** The event stream polls `events.ndjson` from a byte offset. Simple
  and replayable, but it adds polling latency and assumes the API shares a disk with the run.
- **SQLite by default.** Zero setup and fine for one user; teams point `database.url` at
  Postgres for concurrent writers.

## Extending

- **New framework:** for anything that writes JUnit XML, the `command` framework plus
  `junit_xml:` globs is usually enough. Otherwise ship an adapter in your own package under
  the `qasight.frameworks` entry point group (`qasight/plugins.py`, example in
  `examples/plugin/`), or add a built-in one in `qasight/frameworks/` and its name to
  `SUPPORTED_FRAMEWORKS` in `config/schema.py`.
- **New reporter:** a `BaseReporter` subclass under the `qasight.reporters` entry point group,
  or a built-in in `qasight/reporting/reporters/` registered in `SUPPORTED_REPORTER_TYPES`.
- **New persistence target:** implement `PersistenceBackend` (`persistence/backend.py`) and
  add it to `composite_backend()`.

Deeper notes live in the docs vault, starting at `docs/Index.md`
(`docs/Architecture/Architecture Overview.md`, `docs/Architecture/Deep Dive - Execution Logic.md`).
