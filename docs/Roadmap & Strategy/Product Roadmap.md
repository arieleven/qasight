---
type: roadmap
status: current
created: 2026-06-25
updated: 2026-11-12
---

# Product Roadmap

Where QASight (`qasight`, CLI `qasight`) stands today and what comes next. Open engineering debt lives in [Technical Debt Tracker](../Testing%20Workflows/Technical%20Debt%20Tracker.md); the per-phase release checklists from the original plan are in the [Archive](../Archive/README.md).

## Where it is now

**One engine behind every surface.** `qasight.yaml` → `CycleRunService` → `engine.run_plan()` → framework adapters → reporting and persistence. The CLI, the FastAPI backend and the CI wrappers all start runs this way; the Docker-based second execution stack and its `uqo run --config` contract were removed after 1.0.0. See [Architecture Overview](../Architecture/Architecture%20Overview.md).

| Area | State |
|------|-------|
| Frameworks | `pytest`, `behave`, `behavex`, plus `command` for any other runner with JUnit XML import ([Command Adapter and JUnit Import](../Specs%20&%20ADRs/Command%20Adapter%20and%20JUnit%20Import%20-%202026-09-25.md)) |
| CLI | `qasight run` (Rich, `--stream`, `--ci` NDJSON), reports, diff/summary, config, doctor/clean/watch/init; exit codes `0`–`4` ([Command Reference](../CLI%20Commands/Command%20Reference.md)) |
| Storage | SQLite by default, PostgreSQL/MySQL through the repository layer ([Repository Pattern](../Specs%20&%20ADRs/Repository%20Pattern%20-%20Database-Agnostic%20Refactor.md)). Docker and MinIO are not required |
| UI | React + Vite + Tailwind frontend on the FastAPI `/api/v1` API; frontend types generated from the OpenAPI schema ([Phase 5 UI Redesign](../Specs%20&%20ADRs/Phase%205%20UI%20Redesign%20-%20Cycles-First%20Navigation.md)). The Streamlit prototype was removed |
| Analytics | Dashboard, run-to-run delta with per-stage and per-test changes ([Delta Comparison Policy](../Specs%20&%20ADRs/Delta%20Comparison%20Policy.md)), test pyramid, flaky test detection ([Open Source Features](../Specs%20&%20ADRs/Open%20Source%20Features%20-%202026-10-19.md)) |
| AI | Bring-your-own-key failure summaries ([Phase 4 BYOK and Failure Analysis](../Specs%20&%20ADRs/Phase%204%20BYOK%20and%20Failure%20Analysis.md)) |
| CI | GitHub Action and GitLab template wrapping `qasight run --ci` ([CI-CD Pipeline Setup](../Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md)) |
| Demo | A Pages pipeline runs qasight on itself and on the deliberately broken fake-api app, then publishes the React UI as a read-only snapshot ([GitLab Pages Demo](../Processes%20&%20Guides/GitLab%20Pages%20Demo.md)) |
| Distribution | 1.2.0 is released: PyPI (`qasight`), GHCR images, and the `v1` Action tag; a GitHub Release publishes all of them ([Publishing to PyPI](../Processes%20&%20Guides/Publishing%20to%20PyPI.md)) |

## Next

The current ranked list is [Adoption Blockers - 2026-11-12](../Specs%20&%20ADRs/Adoption%20Blockers%20-%202026-11-12.md): what keeps teams from picking QASight first, and which of those code can fix (single sign-on, one-click hosting, live results from pytest, the scorecard in pull requests). What stays stable while it changes is in the [Stability and Deprecation Policy](../Processes%20&%20Guides/Stability%20and%20Deprecation%20Policy.md).

Earlier lists, mostly done:

The product-side priorities are the Now / Next / Later roadmap in the [Product Review](../product-review.md) (2026-10-24): failing tests first on the run page, the UI shipped in the wheel (`qasight ui`), a main-branch baseline for diffs and the PR comment, and a per-test page. Since this note was last updated, ingest, the team server, parallel stages (`max_parallel_stages`), the API run queue and the security switches all shipped; see the [Specs & ADRs](../Specs%20&%20ADRs/README.md).

The next ranked list is [Feature Gaps and Quick Wins](Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md) (2026-10-31): sixteen small fixes from a new user's first ten minutes, and fourteen gaps against Trunk, Datadog, ReportPortal, Allure TestOps, Currents and others, led by a better flaky signal, quarantine from the UI, issues for flaky tests and an MCP server. [Paid Features to Offer Free](Paid%20Features%20to%20Offer%20Free%20-%202026-10-31.md) adds what competitors charge for that QASight could give away, led by code coverage history, a weekly digest and redaction before storing. [New User Journey Audit](New%20User%20Journey%20Audit%20-%202026-11-01.md) (2026-11-01) walks a new user's twelve steps and ranks what could be automated, led by pointing everything at `qasight init`, `qasight init --pr` and browsing CI history without a server.

Still open from the earlier list:

- **Concurrent run guard for the CLI.** The API queues runs, but two CLI runs of one cycle on one machine still share `artifacts/<cycle>/`. A lock file per cycle in `CycleRunService.run()` is the smallest fix.
- **Smaller items** (signal-aware exit codes, reporter failure policy): see [Technical Debt Tracker](../Testing%20Workflows/Technical%20Debt%20Tracker.md).

## How it got here

The project started as "UQO", a Docker-and-Streamlit platform, and was delivered in four phases: packaging and a database-agnostic repository layer; drop-in CI wrappers; the React UI, delta engine and unified dashboard; then BYOK AI failure analysis. Each phase had a release checklist; those checklists and the dated plans and audits behind them are kept in the [Archive](../Archive/README.md).
