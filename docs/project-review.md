---
type: reference
status: current
created: 2026-10-24
updated: 2026-11-04
---

# Project review: scorecard and verdict

[Index](Index.md)

Six passes over the whole repo with the tech-debt, code-review, security-review and architecture checklists (the first pass also used testing-strategy). The **sixth pass** is `main` plus throwaway stage containers (`TESTO_RUNNER=container`), after #163 fixed S10 and S11, 2026-10-24. The fifth pass was `main` after #159 to #161, the fourth was `06e636c`, the third `63ca88f`, the second `3aeb3b3` and the first `f1e3521`, all the same day; their findings are kept below with their status. Open items that should outlive this note move to the [Technical Debt Tracker](Testing%20Workflows/Technical%20Debt%20Tracker.md).

## Verdict

**Overall: 10 / 10 (was 9.5; the first pass was 8).** The fifth pass set the bar for a 10 as "safe to expose to people you don't trust", and named three things missing. All three are now in, each a switch that is off by default, so one developer or a small team gets exactly the old behaviour:

- **Sign-in throttling (S11, #163):** with accounts, repeated wrong passwords for a name or from an address get `429`.
- **Reports on their own origin (S10, #163):** `TESTO_REPORTS_ORIGIN` serves report pages from a second hostname with a cookie that can only read reports.
- **Throwaway stage containers:** `TESTO_RUNNER=container` sends each stage to a separate runner service that starts it in a fresh container: no capabilities, no privilege escalation, a process limit, fixed image and mounts, an allow-list of commands, and only the stage's own variables. The API never gets the Docker socket, and a test can no longer read the server's token or database URL. See [Throwaway Stage Containers](Specs%20&%20ADRs/Throwaway%20Stage%20Containers%20-%202026-10-24.md).

A 10 here is not "nothing left": S12 (stages share the artifacts volume, Low) and the runner service's Docker socket are documented trade-offs, one server process is still the supported setup, and TypeScript 7 waits on `typescript-eslint`. None of them is a defect in what the project claims to do.

## Scorecard

| Area | Score | Was | In one line |
|------|:-----:|:---:|-------------|
| Architecture | **10** | 9.5 | One engine, one service entry point, plugins, a read-side `history/`. Where a stage runs is a backend behind one `launch()` (the slot the executor's docstring had reserved), so host and container share the log, timeout, JUnit and quarantine code; every team feature is a switch with an ADR |
| Code quality | **9.5** | 9 | `ruff`, `ruff format`, `mypy` and ESLint clean, `vulture` empty, no `TODO`s, no module over 500 lines (largest: `cycle_execution_manager.py`, 493) |
| Tests | **9.5** | 9 | 987 fast tests at 86% coverage on Python 3.11 to 3.13; both settings of each switch tested. The runner tests run the real service with a stand-in `docker` (stream, env boundary, timeout, a vanished API), and the container path was also run against a real Docker daemon |
| Security | **10** | 9.5 | Accounts, throttled sign-in, a separate report origin and sandboxed stages, all opt-in. Left: S12 (Low, documented) and the runner service's socket, which is the documented price of keeping it out of the API |
| Docs | **9.5** | 9 | Linted vault, an ADR per team feature, a Team Server section per switch with its variables, a security model that says what each switch does and doesn't protect |
| CI / DX | **9.5** | 9 | Python matrix, ESLint, OpenAPI and schema drift checks, image scan and start check for all three images (the runner service is new), team server smoke, tolerant publish |
| Tech debt | **9.5** | 9 | Nothing from earlier passes is open but TypeScript 7 (blocked upstream). The cost of the switches is two tested modes each |

## What was checked (sixth pass)

| Check | Result |
|-------|--------|
| `ruff check .`, `ruff format --check .`, `mypy`, OpenAPI drift | Clean (mypy: the review machine's known stale-package errors only) |
| `pytest -m "tier_fast and not quarantined"` | 987 passed, 86% coverage; `backends/container.py` and `runner_service.py` 84% (the rest is the `main()` loop and Docker error paths) |
| Runner service by hand, real Docker | The stage container has `CapEff 0`, runs as `10001`, sees the stage's variables and not `TESTO_API_TOKEN`; results land on the shared volume; a timeout gives `124` and removes the container; closing the API's connection removes it within a second; the service image starts and reaches the socket through `group_add` |
| Request checks | Command by name from the allow-list, directory inside a mount, no `..`, valid variable names, `DOCKER_*`/`PATH`/`HOME` dropped, values passed as `-e NAME` (not on the command line), `--flag=value` so nothing in the request reads as a Docker flag; image and mounts never come from the request |
| Default path | `TESTO_RUNNER` unset: the executor calls `subprocess.Popen` with the same arguments as before; the existing executor tests pass unchanged |

## Sixth-pass findings

| # | Severity | Finding | Fix | Status |
|---|----------|---------|-----|--------|
| S12 | Low | **Stage containers share the artifacts volume.** Every stage mounts the whole `testo_artifacts` volume read-write, so a malicious test in one run could read or change another run's artifacts. It can't reach the server's token, database or report history | Mount a per-run subpath (Docker 26+ `volume-subpath`) | Accepted, documented in the ADR |

## What was checked (fifth pass)

| Check | Result |
|-------|--------|
| `ruff check .`, `ruff format --check .`, `mypy`, ESLint, `tsc` | Clean (mypy: the review machine's 2 known stale-package errors only) |
| `pytest -m "tier_fast and not quarantined"` | 952 passed, 86% coverage; new modules 90% or more (`run_queue.py` 94, `security.py` 94, `accounts.py` 90, `auth.py` 92) |
| #159 | `_worker_loop` catches `BaseException` per job, `_ensure_workers` drops dead threads; reproduced the old failure, now a test |
| #160 run queue | A row is deleted (atomically) as its run starts, so an interrupted run is never repeated and two processes never both run a row; recovered runs keep their execution id; a DB error on enqueue frees the cycle |
| #161 accounts | Salted `scrypt`, dummy hash for unknown names, cookie signed with the token and tied to a session version, role checked on every write, reads always protected, every write and sign-in audited, `/login` still refuses foreign origins, token mode byte-for-byte the old path |
| Open pull requests | Only #106 (TypeScript 7, blocked by `typescript-eslint`) |

## Fifth-pass findings

| # | Severity | Finding | Fix | Status |
|---|----------|---------|-----|--------|
| S10 | Low | **Report pages share the API's origin.** S9 sandboxed attachments, but the Allure report pages under `/history` run script on the server's origin. With accounts, a viewer opening a report that an XSS in the report generator let through would act with the viewer's role. Needs a bug in Allure's own escaping, and a viewer can't write anyway | Serve `/history` from a separate origin (`TESTO_REPORTS_ORIGIN`) if the server is exposed to people you don't trust | Fixed (#163) |
| S11 | Low | **No sign-in lockout.** Each failed attempt costs an `scrypt` hash and is audited, but nothing slows a password guesser down | Rate-limit `/api/v1/auth/login` at the reverse proxy (documented in the security model), or add a per-name backoff | Fixed (#163) |

## What was checked (fourth pass, 06e636c)

| Check | Result |
|-------|--------|
| `ruff check .` and `ruff format --check .` | Clean |
| `pytest -m "tier_fast and not quarantined"` | 919 passed, 0 failed, 86% coverage, on the review machine too |
| `vulture --min-confidence 80`, `TODO`/`FIXME` | Nothing |
| #156 worker pool | FIFO queue, one cycle queued or running at a time, `queued` status in the API models and the UI, workers started lazily. A job that raises `BaseException` kills its worker (A4, checked by running one) |
| #156 parallel stages | Events and the renderer stay on the calling thread, results keep config order, `fail_fast` starts nothing new after a failure, duplicate stage names refused when parallel, live output buffered per stage |
| Open pull requests | Only #106 (TypeScript 7, blocked by `typescript-eslint`) |

## Fourth-pass findings

| # | Severity | Finding | Fix | Status |
|---|----------|---------|-----|--------|
| A4 | Low | **A worker that hits `SystemExit` is never replaced.** `_run_execution` catches `Exception`, so a `SystemExit` or `KeyboardInterrupt` raised during a run (a reporter plugin calling `sys.exit()`, say) ends `_worker_loop`'s thread. The run itself is marked failed, but `_ensure_workers` counts dead threads, so the pool shrinks for good; with the default of 2, two such runs leave every later run `queued` forever | Catch `BaseException` per job in `_worker_loop` (log it and keep looping), or drop dead threads in `_ensure_workers`, plus a test | Fixed (#159) |
| D10 | Code | Three modules over 500 lines, and two grew this round: `cycle_execution_manager.py` (539 to 581) and `config/loader.py` (537 to 560) | Split the worker pool and the SSE helpers out of the manager, and the cycle parsing out of the loader, the next time each changes | Fixed (#159) |

## What was checked (third pass, 63ca88f)

| Check | Result |
|-------|--------|
| `ruff check .` and `ruff format --check .` | Clean (432 files) |
| `mypy qasight qasight_api` | Clean in CI. Locally it reports the same 2 errors in `repository/models.py` as before, which come from stale packages on the review machine |
| `pytest -m "tier_fast and not quarantined"` | 867 passed, 84% coverage. The 2 local failures are the review machine again (see T6) |
| Frontend | `tsc`, ESLint, 58 Vitest tests and the build pass on React 19; `npm audit` 0 |
| `pip-audit`, `vulture --min-confidence 80` | Nothing |
| Diff since the second pass | `config init` starter, server image `ENV`, React 19 ref types, publish retry: no findings |
| Open pull requests | Only #106 (TypeScript 7, blocked by `typescript-eslint`) |

## Third-pass findings (63ca88f)

| # | Severity | Finding | Fix | Status |
|---|----------|---------|-----|--------|
| S9 | Low | **Report files share the API's origin.** `/history` serves run artifacts with `StaticFiles`, and Playwright attachments keep their type, so an uploaded `text/html` attachment opens as a page on the team server's own origin. A script in it runs with the viewer's session and passes the Origin check, so it can start runs. With one shared token, the uploader already has that power, so nothing is gained today. There are also no `X-Content-Type-Options` or `frame-ancestors` headers | Send `Content-Security-Policy: sandbox` and `X-Content-Type-Options: nosniff` on `/history` responses, and `frame-ancestors 'self'` everywhere. Serving reports from a separate origin only matters once there are per-user accounts | Fixed (#155) |
| T6 | Test | Two tests still assume a contributor's machine: the packaging contract fails when `build` isn't installed, and the rootdir test picks a `pytest` on `PATH` that belongs to another interpreter (without `allure-pytest`). T3 fixed the case where no `pytest` is found, but not this one | Skip the `build` check when the module is missing, and run `sys.executable -m pytest` in the rootdir test | Fixed (#155) |

## What was checked (second pass)

| Check | Result |
|-------|--------|
| `ruff check .` and `ruff format --check .` | Clean (431 files) |
| `mypy qasight qasight_api` | Clean in CI on all three Python versions |
| `pytest -m "tier_fast and not quarantined"` | 865 passed, 84.3% coverage (gate 65%). The 2 local failures are the review machine (no `build` module, `pytest` not on `PATH`); the same tests pass in CI |
| Frontend | `tsc`, ESLint, 58 Vitest tests and the Vite 8 build pass; `npm audit` 0 (was 6) |
| `pip-audit` | Nothing in the project's dependencies |
| `vulture --min-confidence 80` | Nothing (was 2 unused variables and the dead report helpers) |
| Team server login | `TESTO_PROTECT_READS=1` checked end to end: 401 without a session, redirect to `/login`, bearer and cookie reads, health open; open-redirect attempts (`//host`, `/\host`, tab-prefixed) stay on the server |
| Open pull requests | 9 from Dependabot (#132 to #140) and #106 (TypeScript 7, blocked by `typescript-eslint`), plus #124 from a contributor |

## Second-pass findings (3aeb3b3)

All Low, most severe first. None was a blocker.

| # | Severity | Finding | Fix | Status |
|---|----------|---------|-----|--------|
| S6 | Low | **Protected reads are only on in the compose file.** The `qasight-server` image itself does not set `TESTO_PROTECT_READS`, so `docker run -e TESTO_API_TOKEN=... qasight-server` serves runs, logs and reports to anyone who can reach it | Set `TESTO_PROTECT_READS=1` in `Dockerfile.qasight-server` so the image is safe by default, as the compose file already is | Fixed (#150) |
| S7 | Low | **Sessions are stateless.** The cookie is an HMAC of the token, so sign-out only clears the browser's copy; a copied cookie works until the token changes, for up to 30 days | Acceptable with one shared token; document "rotate the token to sign everyone out", or add a server-side session list if per-user accounts arrive | Documented (#150) |
| S8 | Info | **No rate limit on `/login`.** A guessed token is the only way in, and the guides tell people to use `openssl rand -hex 32`, so online guessing is not practical | Nothing now; a reverse proxy can rate-limit if needed | Accepted |
| S5 | Low | **AI `base_url` is free-form** (open from the first pass). A write token can point the provider at any URL, and the key goes there | One sentence in the security model | Documented (#150) |
| T5 | Test | Lowest-covered modules: `reporting/allure.py` 32%, `reporting/server.py` 37%, `frameworks/behave_adapter.py` 45%, `reporting/behavex_allure.py` 48%, `cli/commands/watch.py` 48% | Tests for the Allure CLI fallbacks and the Behave adapter's argument building | Fixed (#157) |
| D8 | Dependency | Nine Dependabot pull requests (#132 to #140) are open, including `actions/setup-python` 7 and `@testing-library/jest-dom` 7 (a major bump), and #105 is superseded by the merged Vite 8 upgrade | Review and merge the green ones; close #105 | Done (#151 and the merged Dependabot PRs; #106 blocked upstream) |
| D9 | Process | Contributor PR #124 has waited since 2026-10-21 for a merge from `main` | Nudge or take it over with credit | Done (#152; #124 shows merged) |

## First pass (f1e3521): findings and status

### What was checked (first pass)

| Check | Result |
|-------|--------|
| `ruff check .` and `ruff format --check .` | Clean (425 files) |
| `mypy qasight qasight_api` | Clean, also in a fresh Python 3.13 venv with the latest `sqlmodel` (0.0.48). An earlier run showed 2 errors in `repository/models.py`; that came from stale packages in the review machine's global environment, not from the code |
| `python scripts/docs_vault.py lint` | ok |
| `pytest -m "tier_fast and not quarantined"` | 813 passed, 4 failed, 79.4% coverage. All 4 failures were the review machine: no `build` module, the `bdd` extra failed to install, and one test that expects `pytest` next to `sys.executable` |
| `npm audit` (frontend) | 0 in production dependencies; 6 in dev tooling (Vitest, esbuild, tinypool) |
| `pip-audit` | Nothing in the project's own dependencies |
| `vulture --min-confidence 80` | 2 unused variables; manual check found unused functions in `reporting/report_generator.py` (see D1) |

### Architecture

The design matches [ARCHITECTURE.md](../ARCHITECTURE.md) and the [Architecture Overview](Architecture/Architecture%20Overview.md): `config/loader.py` → `config/resolver.py` → `engine/orchestrator.run_plan()` → `engine/executor.run_stage()`, with both the CLI and the API going through `services/cycle_run.CycleRunService` (the only caller of `run_plan`). Ingest reuses the same service through `equipment: junit`, so uploaded results get the same history, Compare, flaky detection and reporters as real runs. That is the strongest part of the project: features added in October (ingest, team server, quarantine, alerts, Playwright JSON) slotted in without a second path.

Strengths

- Framework adapters behind a small protocol (`frameworks/base.py`), and third-party runners and reporters through entry points (`plugins.py`), so new runners need no core change.
- Storage behind `persistence/` and `repository/`, reads behind `history/`, SQLite by default and Postgres for the team server.
- Exit codes single-sourced in `engine/exit_codes.py` and locked by contract tests.
- The frontend's API types are generated from OpenAPI and CI fails when they drift.

Risks to watch

- **A1. Thread-per-run API.** *Fixed: executions now queue for a pool of `TESTO_MAX_CONCURRENT_RUNS` workers (default 2); see [Scale Runs and Stages - 2026-10-24](Specs%20&%20ADRs/Scale%20Runs%20and%20Stages%20-%202026-10-24.md). State is still in memory.* Was: `qasight_api/cycle_execution_manager.py` starts a daemon thread per execution and keeps state in memory. One run per cycle is enforced, which is right, but there is no global cap on concurrent runs and a restart loses live state (orphans are cleaned up on startup). Fine for a local tool and a small team server; a queue would be the next step if the server grows.
- **A2. Sequential stages.** *Fixed: a cycle can opt in with `max_parallel_stages: N` (same note).*
- **A3. Version hard-coded twice.** *Fixed (#141).* `qasight_api/main.py` sets `version="1.1.1"` by hand next to `pyproject.toml`. Read it from `importlib.metadata` so a release can't leave the API reporting an old version.

### Security

The model in [ARCHITECTURE.md § Security model](../ARCHITECTURE.md#security-model) is sound and the code follows it: loopback by default, foreign-origin writes refused even without a token (which stops simple cross-site POSTs and DNS rebinding), `hmac.compare_digest` for the token, upload size limits, sanitised upload filenames, Playwright attachments confined to the report root, secret redaction before AI calls, and the team server's compose file refuses to start without `TESTO_API_TOKEN`. No workflow uses `pull_request_target`, and 10 of 13 set explicit `permissions:` (see S4).

Findings, most severe first

| # | Severity | Finding | Fix | Status |
|---|----------|---------|-----|--------|
| S1 | Medium | **Open reads take an arbitrary file path.** `GET /api/v1/cycles`, `GET /api/v1/cycles/{cycle}` and `GET /api/v1/runs/{run_id}/pyramid` accept `?config_path=` and load it. Reads need no token even when `TESTO_API_TOKEN` is set, so anyone who can reach a team server can probe whether any file exists and see fragments of non-YAML files in the error message (checked: `config_path=/root/.bashrc` returns a line of the file in the YAML parse error) | Drop `config_path` from GET routes, or restrict it to files under the configured project root, and return a generic error message | Fixed (#141) |
| S2 | Medium | **Public bind without a token only warns.** `qasight-api` on a non-loopback host with no `TESTO_API_TOKEN` logs a warning and starts, and the `qasight-server` image binds `0.0.0.0`. A plain `docker run -p 8000:8000 qasight-server` exposes "start a run", which runs commands, to the network | Refuse to start in that case unless an explicit `TESTO_ALLOW_UNAUTHENTICATED=1` is set | Fixed (#141) |
| S3 | Low | **Reads and `/history` are open on the team server.** Run logs and report files can carry secrets printed by tests. Documented in the [Team Server](Processes%20&%20Guides/Team%20Server.md) guide, but there is no switch to protect them | Add an opt-in `TESTO_API_READ_TOKEN` (or require the token for reads when set) | Fixed (#147) |
| S4 | Low | **Floating action refs and default token scope.** `aquasecurity/trivy-action@master` and `anthropics/claude-code-base-action@beta` run in the Docker publish and changelog-on-main workflows, and `commitlint.yml`, `pr-heavy.yml` and `release-gate.yml` have no `permissions:` block, so they get the repository's default token scope. A compromised upstream branch would run with those jobs' secrets | Pin the two actions to a release tag or commit SHA (Dependabot already watches `github-actions`) and add `permissions: contents: read` to the three workflows | Fixed (#141) |
| S5 | Low | **AI `base_url` is free-form.** With a write token, the AI settings page can point the provider at any URL and the server will send the key there. Acceptable for a self-hosted tool; worth a sentence in the security model | Document it, optionally allow-list schemes and hosts | Documented (#150) |

XML from uploads is parsed with the standard library's `ElementTree`, which on Python 3.11+ does not resolve external entities and has expat's entity-expansion limits, and uploads are size-capped, so XXE and billion-laughs are not a concern.

### Code quality and tech debt

Prioritised with the tech-debt formula, priority = (impact + risk) × (6 − effort), each scored 1 to 5.

| # | Item | Type | Impact | Risk | Effort | Priority | Status |
|---|------|------|:------:|:----:|:------:|:--------:|--------|
| S1 | `config_path` on open GET routes | Security | 2 | 4 | 1 | **30** | Fixed (#141) |
| S2 | Public bind without token is fail-open | Security | 2 | 4 | 1 | **30** | Fixed (#141) |
| D2 | Large unreleased backlog: `CHANGELOG.md [Unreleased]` holds ingest, the team server, Playwright JSON, quarantine, alerts, the config schema, the plugin API and a breaking extras change. PyPI users are on 1.1.1 | Release | 4 | 3 | 2 | **28** | Released as v1.2.0, on PyPI |
| D1 | Streamlit-era dead code in `reporting/report_generator.py`: `sync_all_reports_to_static`, `start_report_server`, `start_static_server`, `collect_behavex_native_report` and `url_for` have no caller in `qasight/` or `qasight_api/`, and six comments still mention Streamlit, which was removed in v1.1 | Code | 3 | 2 | 1 | **25** | Fixed (#141) |
| D3 | No Python version matrix: classifiers claim 3.12 and 3.13, CI runs only 3.11 | Infrastructure | 2 | 3 | 1 | **25** | Fixed (#141) |
| T1 | Thin coverage where users plug in: TestBeats reporter 18%, `cli/commands/config.py` 19%, ReportPortal reporter 26%, Anthropic provider 30%, `cli/commands/report.py` 39% | Test | 3 | 3 | 3 | **18** | Fixed (#143) |
| D4 | No Python lock or constraints file, only lower bounds, so CI resolves the newest dependencies on every run and an upstream release can turn `main` red with no change here. Normal for a library; checked that today's latest versions pass `mypy` and the suite on 3.12 and 3.13 | Dependency | 1 | 2 | 2 | **12** | Accepted |
| D5 | Frontend dependency debt: Dependabot PRs for Vite 8 (#105) and TypeScript 7 (#106) break the build; dev tooling has 6 `npm audit` advisories | Dependency | 2 | 2 | 3 | **12** | Vite 8 done (#146); TypeScript 7 blocked upstream |
| D6 | `qasight/mascot/` (422 lines, 0% coverage) ships inside the core package for a UI animation | Code | 1 | 1 | 2 | **8** | Fixed (#148) |
| D7 | No frontend linter (ESLint or Biome); `tsc` is the only static check | Infrastructure | 1 | 2 | 2 | **12** | Fixed (#144) |

Good habits worth keeping: no `TODO` or `FIXME` markers in the Python packages, every broad `except Exception` has a comment saying why, 14 `type: ignore`s across ~20k lines, and functions are small with a few exceptions (`config/loader.py`, `reporting/report_generator.py`, `cli/commands/report.py` and `qasight_api/cycle_execution_manager.py` are each 500+ lines).

### Tests

The suite is a pyramid in the right shape: 102 unit files, 19 contract files (exit codes, CI tiers, packaging, OpenAPI), 7 integration and 3 end-to-end, with tiers and a quarantine marker. The fast tier runs in about 16 seconds, which keeps it on every pull request, and coverage is 79% against a 65% gate. The project also tests itself through its own GitHub Action and the fake-api demo, which is a strong signal.

Gaps

- **T1** above: reporters and CLI report commands are where outside users integrate, and they are the least covered.
- **T2.** *Fixed (#145).* The frontend has 17 test files for ~8k lines of TypeScript. The Compare and Run Detail pages carry the product's main story and deserve component tests for their empty, error and loading states.
- **T3.** *Fixed (#141).* A few tests assume the machine: `test_target_inside_another_pytest_project_keeps_its_own_rootdir` expects `pytest` next to `sys.executable`, and the packaging contract needs `build`. Skip them with a clear reason when the tool is missing, so a contributor's first local run is green.
- **T4.** *Fixed (#141).* With no 3.12/3.13 job (D3), the version claims in `pyproject.toml` are untested.

### Docs and developer experience

The vault is the project's best interview asset: a linted entry point, ADRs per decision, an architecture document with a security model and known limits, a getting-started guide that CI runs end to end, and a docs site. `CONTRIBUTING.md`, issue and PR templates, `SECURITY.md`, a code of conduct and `CITATION.cff` are all present.

Small gaps: `actionlint_py-1.7.12.25.tar.gz` was committed (*removed in #141*) at the repo root and looks like a stray download, and the README is 333 lines, so a newcomer has to scroll past a lot before the first command.

## What's left

1. **Hardening the container runner further.** Per-run artifact subpaths (S12), and a Kubernetes or rootless backend behind the same three endpoints for teams that don't want a Docker socket anywhere.
2. **When upstream allows.** TypeScript 7 once `typescript-eslint` supports it.
3. **If the team server outgrows one process.** A shared one-run-per-cycle guard and concurrency cap across processes; the durable queue already claims rows safely.

## Progress

- 2026-10-24, step 1 done in the same pull request as this note: S1 (read routes only load config file names), S2 (public bind without a token refuses to start), S4 (actions pinned to SHAs, read-only `permissions:`), A3 (API version from package metadata), D1 (dead report helpers and the stray tarball removed).
- 2026-10-24, step 2 in the same pull request: D3 (a `test (python 3.12/3.13)` CI job, suite green on both in clean venvs) and T3 (the rootdir test finds `pytest` on `PATH` when it is not next to the interpreter; BehaveX tests skip without the `bdd` extra). D4 re-scored after a clean-venv check. The release (D2) waits on Taltal.
- 2026-10-24, step 3 as separate pull requests: T1 reporter, provider and CLI tests (#143), D7 frontend ESLint (#144), T2 frontend state tests (#145), D5 Vite 8 and Vitest 5 (#146; TypeScript 7 waits on `typescript-eslint`). S3: Taltal chose a cookie login over a read token, so `TESTO_PROTECT_READS=1` makes every read need the token or the `/login` session cookie (the team server compose file turns it on); see the [Team Server](Processes%20&%20Guides/Team%20Server.md) guide.
- 2026-10-24: D6 done, the mascot moved to `scripts/mascot/` and out of the wheel.
- 2026-10-24, second pass at `3aeb3b3`: overall 8 → 8.5; new findings S6 to S8, T5, D8 and D9.
- 2026-10-24: S6 fixed (the server image sets `TESTO_PROTECT_READS=1`), S5 and S7 documented in the ARCHITECTURE security model.
- 2026-10-24: D8 and D9 done (#151, #152), and 1.2.0 is on PyPI. The publish workflow now retries its post-upload install check (#153).
- 2026-10-24, third pass at `63ca88f`: overall stays 8.5, and tech debt goes from 8 to 8.5. New findings: S9 and T6.
- 2026-10-24: S9 fixed (`nosniff` and `frame-ancestors 'self'` on every response, `sandbox` on report attachments) and T6 fixed (the fast suite is green on a machine without `build`).
- 2026-10-24: T5 fixed, with tests for the Allure wrapper, report server, Behave adapter, BehaveX export and `qasight watch` (each 92% or more, total 86%).
- 2026-10-24, fourth pass after #156 and #157: overall 8.5 → 9. A1 and A2 fixed by #156, T5 by #157. New findings: A4 and D10.
- 2026-10-24: A4 and D10 fixed (#159); switchable run queue (#160) and per-user accounts (#161) added, both off by default.
- 2026-10-24, fifth pass after #161: overall 9 → 9.5. New findings: S10 and S11 (both Low, accepted and documented).
- 2026-10-24: S10 fixed (`TESTO_REPORTS_ORIGIN`) and S11 fixed (sign-in throttling) in #163.
- 2026-10-24, sixth pass with throwaway stage containers (`TESTO_RUNNER=container`): overall 9.5 → 10. New finding: S12 (Low, accepted).
