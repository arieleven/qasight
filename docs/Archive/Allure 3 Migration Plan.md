---
type: archive
status: archived
created: 2026-06-25
updated: 2026-11-04
---

# Allure 3 Migration Plan

**Project:** Testo (`testo-core`) — Unified Quality Orchestration CLI  
**Goal:** Migrate reporting infrastructure from **Allure Report 2 (Java CLI)** to **Allure Report 3 (Node.js CLI)**, and retire **`frankescobar/allure-docker-service`** in the UQO compose stack.  
**Status:** Plan for review — **no implementation in this note.**

## Strategy (WHY)

| Driver | Detail |
|--------|--------|
| Modern UI | Allure 3 (Awesome plugin) replaces the Allure 2 SPA |
| Native history | `historyPath` (JSONL) replaces manual `history/` directory copy |
| Tooling alignment | Node-based CLI matches frontend/React toolchain; Java binary no longer required |
| Docker simplification | Remove third-party Allure Docker Service + MinIO→Allure sync loop |

**Non-goal:** Change how tests emit results. Official Python adapters stay on Allure 2.x and continue writing standard `*-result.json` under `allure-results/`.

## Architecture (three layers)

```mermaid
flowchart TB
  subgraph L1["Layer 1 — Python adapters (frozen)"]
    PY[allure-pytest / allure-behave / allure-python-commons]
    ART[artifacts/.../allure-results/]
    PY --> ART
  end
  subgraph L2["Layer 2 — CLI generation (refactor)"]
    WR[testo_core/reporting/allure.py + server.py]
    RC[allurerc.mjs at repo root]
    NPX[npx allure generate | open | serve]
    WR --> RC --> NPX
  end
  subgraph L3["Layer 3 — Hosting (replace)"]
    MINIO[(MinIO uqo-artifacts)]
    STATIC[Static HTML bundle per run_id]
    URL[ALLURE_SERVER_URL or object URL]
    NPX --> STATIC --> MINIO --> URL
  end
  ART --> WR
```

## Related vault notes

| Topic | Note |
|-------|------|
| CLI flags & `testo report` | [Command Reference § `testo report`](../CLI%20Commands/Command%20Reference.md#qasight-report) |
| Exit codes & Allure missing | [Troubleshooting and Error Codes](../CLI%20Commands/Troubleshooting%20and%20Error%20Codes.md) |
| Post-run reporters | [QA Strategies § Post-run reporters](../Testing%20Workflows/QA%20Strategies.md#reporters-after-run) |
| UQO compose stack (today) | `ARCHITECTURE.md`, `docker-compose.yml` |
| Official migration | https://allurereport.org/docs/v3/migrate/ |
| Configuration reference | https://allurereport.org/docs/v3/configure/ |

---

## Layer 1 — Python adapters (DO NOT TOUCH)

### Step 1.1 — Verify dependencies (read-only)

Confirm in `pyproject.toml` (already present as of this plan):

| Package | Constraint |
|---------|------------|
| `allure-pytest` | `>= 2.15.0` |
| `allure-behave` | `>= 2.15.0` |
| `allure-python-commons` | `>= 2.15.0` |

**Action:** Document in release notes only. Do **not** bump these for Allure 3; Allure 3 explicitly consumes the same result JSON.

### Step 1.2 — Confirm artifact layout (unchanged)

Testo already standardizes output paths (see [Deep Dive - Execution Logic](../Architecture/Deep%20Dive%20-%20Execution%20Logic.md)):

- Per-stage: `artifacts/<cycle>/<stage>/allure-results/<framework>/`
- Docker/headless flat layout: `artifacts/allure-results/<framework>/`
- Env: `UQO_SHARED_ALLURE_RESULTS_DIR`

**Action:** None on collectors (`testo_core/reporting/collector.py`) or executor env injection (`testo_core/engine/executor.py`).

### Step 1.3 — Reconcile history strategies (plan only)

Today Testo injects Allure 2-style `history/` folders before generate (`testo_core/reporting/history_inject.py`). Allure 3 prefers a single **`historyPath`** JSONL file updated on each `generate`.

| Approach | Recommendation |
|----------|----------------|
| Short term | Keep `history_inject.py` until Layer 2 proves Allure 3 `historyPath` on a shared file |
| Target | One repo-level `historyPath` (e.g. `reports/allure-history.jsonl`) in `allurerc.mjs`; retire folder copy when trend graphs match expectations |

---

## Layer 2 — CLI generation (REFACTOR)

### Inventory — where Java `allure` is invoked today

| Module | Role |
|--------|------|
| `testo_core/reporting/allure.py` | **Canonical** wrapper: `allure generate`, `allure serve`; `AllureCLINotFoundError` |
| `testo_core/reporting/server.py` | `allure open` for generated HTML; stdlib fallback |
| `testo_core/reporting/allure_history_serve.py` | `testo report compare` pipeline: generate + serve |
| `testo_core/reporting/reporters/allure_reporter.py` | Post-run reporter (`testosterone.yaml` `reporters: allure`) |
| `testo_core/report_generator.py` | **Legacy UQO path**: `allure generate --clean --single-file` for Streamlit/Docker platform |
| `testo_core/cli/commands/doctor.py` | `shutil.which("allure")` health check |
| `testo_core/cli/commands/report.py` | User-facing help text for Allure dashboard |

All subprocess argv construction for Allure should remain centralized in **`allure.py`** (plus legacy `report_generator.py` until UQO path is unified).

### Step 2.1 — Add Node.js toolchain at repo root

**Deliverables:**

1. `package.json` with devDependency `"allure": "^3.x"` (pin after spike).
2. Lockfile committed (`package-lock.json`) for reproducible CI.
3. Document **Node.js >= 18** (or version required by `allure` npm package) in `README.md` and `testo doctor`.

**Invocation policy (recommended):**

```bash
# From repository root (where allurerc.mjs lives)
npx allure <command> [args]
```

Optional env override for advanced users:

- `TESTO_ALLURE_CLI` — default `npx`; value `allure` if global v3 is installed.

### Step 2.2 — Baseline `allurerc.mjs` (repo root)

> **Naming:** Allure 3 discovers `allurerc.mjs` by default. The user brief mentioned `allure.config.mjs`; use the **official** filename unless you pass `--config ./allure.config.mjs` on every invocation.

**Starter config** (align with Testo defaults):

```javascript
import { defineConfig } from "allure";

export default defineConfig({
  name: "Testo — Unified Quality Report",
  output: "./reports/allure",           // matches testosterone.yaml reporters
  historyPath: "./reports/allure-history.jsonl",
  plugins: {
    awesome: {
      options: {
        reportLanguage: "en",
        open: false,                    // Testo controls browser open via CLI flags
        singleFile: false,              // legacy UQO used --single-file; decide per surface
      },
    },
  },
});
```

**Follow-up spikes:**

- Map any repo `categories.json` into `categories.rules` (see [migration guide](https://allurereport.org/docs/v3/migrate/#categories)).
- Decide whether `testo report --format html` for **legacy** `report_generator.py` still needs `singleFile: true` for Streamlit embedding.

### Step 2.3 — Refactor `testo_core/reporting/allure.py`

| Task | Detail |
|------|--------|
| CLI resolution | `resolve_allure_argv() -> list[str]` → `["npx", "allure"]` or `["allure"]` |
| Version probe | `npx allure --version`; warn on v2 (`doctor`) |
| `generate_html` | `npx allure generate <dirs> --output <out>` + `--config` if not cwd repo root |
| CWD | `subprocess` with `cwd=repo_root` so `allurerc.mjs` is picked up |
| Flags | Drop Allure 2-only flags (`--clean` semantics may differ — verify via `allure generate --help`) |
| Errors | Extend `AllureCLINotFoundError` → `AllureCLIError` with messages for missing **Node**, **npm**, or **allure** package |

### Step 2.4 — Refactor `server.py` and serve paths

| Allure 2 | Allure 3 (verify in spike) |
|----------|----------------------------|
| `allure open <dir> --host --port` | Likely unchanged; confirm port flags |
| `allure serve <result dirs>` | Confirm equivalent; may prefer generate + open |

Keep **stdlib static fallback** when CLI missing (CI without Node).

### Step 2.5 — Unify legacy `report_generator.py`

Platform Docker runs still call `generate_allure_html()` with `--single-file`. Plan:

1. Delegate to shared `allure.py` wrapper with reporter-specific options, **or**
2. Add `plugins.awesome.options.singleFile` override via a second config profile / CLI `--config`.

Avoid duplicating subprocess logic in two modules long term.

### Step 2.6 — CLI help, doctor, and exit codes

| Surface | Update |
|---------|--------|
| `testo doctor` | Check `node`, `npm`, `npx allure --version`; downgrade Java `allure` on PATH to WARN |
| `testo report --help` | "Requires Node.js and `npm install` at repo root" instead of "Install Allure CLI (Java)" |
| [Troubleshooting and Error Codes](../CLI%20Commands/Troubleshooting%20and%20Error%20Codes.md) | New subsection: Node/npm missing → exit `3` (`INFRA_FAILURE`) |
| [Command Reference](../CLI%20Commands/Command%20Reference.md) | Report flags unchanged; dependency section updated |

**Error message template:**

```text
Allure Report 3 CLI not available. From the repository root run:
  npm install
  npx allure --version
Or set TESTO_ALLURE_CLI=allure after installing Allure 3 globally (npm install -g allure).
```

### Step 2.7 — Tests

| Area | Action |
|------|--------|
| Unit | Mock `subprocess` in existing reporter tests; assert argv contains `npx` + `allure` |
| Integration | Optional marker `@pytest.mark.allure3` requiring Node in CI |
| Contract | `index.html` exists under `reports/allure` after sample cycle |

### Step 2.8 — CI / developer docs

- `README.md` quickstart: install Node, `npm ci`, then `testo report`
- [CI-CD Pipeline Setup](../Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md): add Node setup action before report step
- `.gitignore`: ignore `reports/allure-history.jsonl` if local-only (or commit for demo — team decision)

---

## Layer 3 — Docker integration (REPLACE)

### Inventory — current stack

`docker-compose.yml`:

| Service | Purpose |
|---------|---------|
| `allure` (`frankescobar/allure-docker-service`) | Auto-generate reports from `/app/projects` |
| `allure-sync` | Mirror MinIO `projects/` → Allure container volume |

**Consumers of Allure Docker URLs:**

| File | Pattern |
|------|---------|
| `testo_api/routes/history.py` | `{ALLURE_SERVER_URL}/allure-docker-service/projects/{run_id}/reports/latest/index.html` |
| `testo_core/services/dashboard_service.py` | Same |
| `testo_ui/streamlit_app.py` | Same |
| `README.md` / `ARCHITECTURE.md` | Documented contract |

**Note:** `testo_core/runners.py` uses Docker SDK for **test execution containers**, not for Allure Server — do not conflate.

### Step 3.1 — Target hosting model

**Recommended:** **pre-generated static HTML in MinIO** (already used for artifacts).

```text
Run completes → upload raw results to s3://uqo-artifacts/projects/<run_id>/results/
             → run npx allure generate (host or CI job) with shared historyPath
             → upload reports/allure/ tree to s3://uqo-artifacts/projects/<run_id>/reports/latest/
             → ALLURE_SERVER_URL points at MinIO public URL or nginx reverse proxy
```

| Option | Pros | Cons |
|--------|------|------|
| **A. MinIO public object URLs** | No extra container; aligns with existing bucket policy | No built-in directory index; need predictable `index.html` path |
| **B. nginx static container** | Familiar URL layout; cache headers | Another service to maintain |
| **C. Allure 3 “Service” container** | Only if official image exists at migration time | TBD — verify Allure docs/releases before committing |

**Decision gate:** Spike whether Allure 3 ships an official long-running “service” image. If not, implement **A** or **B**.

### Step 3.2 — Remove compose services

1. Delete `allure` and `allure-sync` services from `docker-compose.yml`.
2. Remove `allure_projects` volume.
3. Update `ARCHITECTURE.md` runtime services diagram.

### Step 3.3 — Change upload/sync pipeline

Locate platform report sync (see `testo_core/report_generator.py`, `testo_core/run_history.py`, `testo_core/s3_client.py`):

| Today | Target |
|-------|--------|
| Upload raw results only; Docker service generates HTML | Host/CI runs `npx allure generate` then uploads HTML tree |
| `allure-sync` mirrors MinIO → Docker | Delete sync; HTML already in MinIO |

Ensure `historyPath` file lives on a **persistent volume** (MinIO object `projects/_shared/allure-history.jsonl` or Postgres-backed export) so trends survive container restarts.

### Step 3.4 — URL contract migration

Introduce stable link shape (example):

```text
{ALLURE_SERVER_URL}/projects/{run_id}/reports/latest/index.html
```

Implementation options:

- MinIO: `http://localhost:9000/uqo-artifacts/projects/<run_id>/reports/latest/index.html`
- nginx: alias `/projects/` → bucket prefix

Update:

- `testo_api/routes/history.py`
- `testo_core/services/dashboard_service.py`
- `testo_ui/streamlit_app.py`
- API contract tests: `tests/contract/api/test_history_contract.py`

Maintain **backward-compatible redirect** or env `ALLURE_URL_LEGACY_PREFIX` for one release if external bookmarks exist.

### Step 3.5 — Environment variables

| Variable | Change |
|----------|--------|
| `ALLURE_SERVER_URL` | Keep; repoint to static host (port may move off `5050`) |
| `TESTO_ALLURE_HISTORY_PATH` | Optional override for shared JSONL |
| Document | Remove references to `allure-docker-service` path segment |

### Step 3.6 — Local dev workflow

```bash
docker compose up -d postgres minio minio-init   # no allure services
npm ci
testo run --cycle sample-pytests
testo report --cycle sample-pytests               # Layer 2 local dashboard
```

For platform UI testing with remote reports: run a one-shot upload script or `testo report` + MinIO sync job.

---

## Execution phases (ordered)

| Phase | Scope | Exit criteria |
|-------|--------|---------------|
| **0 — Spike** | Install `allure@3`, manual `npx allure generate` on `artifacts/behavex-flow-tests/.../allure-results` | HTML opens; trends with `historyPath` |
| **1 — Layer 2 core** | `package.json`, `allurerc.mjs`, refactor `allure.py` + `server.py`, doctor/help/docs | `testo report` works without Java `allure` |
| **2 — Layer 2 parity** | `allure_history_serve.py`, `allure_reporter.py`, history strategy | `testo report compare` + YAML reporters pass |
| **3 — Legacy unify** | `report_generator.py` → shared wrapper | UQO/Streamlit path generates v3 HTML |
| **4 — Layer 3** | Compose removal, MinIO HTML upload, URL updates | Dashboard links work; no `frankescobar` image |
| **5 — Cleanup** | Remove `history_inject.py` if redundant; archive Allure 2 docs links | Single history mechanism |

---

## Risk register

| Risk | Mitigation |
|------|------------|
| CI images lack Node | Add Node to runner image checklist ([Release Checklist - Phase 2 Runner Image](Release%20Checklist%20-%20Phase%202%20Runner%20Image.md)) |
| Global Allure 2 on PATH shadows v3 | `doctor` version check; prefer `npx` |
| Delta/compare widgets rely on Allure 2 file layout | Validate `allure_delta_transform.py` + `allure_summary_widgets.py` against Awesome report |
| `--single-file` reports embedded in Streamlit | Explicit `singleFile` plugin option for legacy path only |
| Broken external links to port 5050 | Release note + redirect env |

---

## Documentation updates (required when implementing)

Per `.cursorrules`, any CLI/error-code change must update:

- [Command Reference](../CLI%20Commands/Command%20Reference.md)
- [Troubleshooting and Error Codes](../CLI%20Commands/Troubleshooting%20and%20Error%20Codes.md)
- `README.md` (quickstart + `ALLURE_SERVER_URL`)
- `ARCHITECTURE.md` (compose + URL contract)
- This plan → mark sections **Done** with PR links

---

## Review checklist (for stakeholders)

- [ ] Approve **repo-root `npm` dependency** vs global-only Allure 3
- [ ] Approve **`allurerc.mjs`** output dir `reports/allure` vs `artifacts/.../allure-report`
- [ ] Choose Layer 3 hosting: **MinIO direct** vs **nginx**
- [ ] Decide fate of `history_inject.py` vs Allure 3 `historyPath`
- [ ] Confirm port **5050** can be freed or repurposed

---

**Last updated:** 2026-06-02  
**Owner:** Testo / UQO platform team
