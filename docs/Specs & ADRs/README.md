---
type: spec
status: current
created: 2026-06-25
updated: 2026-11-12
---

# Specs & ADRs

Design decisions that still describe the code. The code is the source of truth; see [Architecture Overview](../Architecture/Architecture%20Overview.md) and [Deep Dive - Execution Logic](../Architecture/Deep%20Dive%20-%20Execution%20Logic.md) for how it fits together.

| Topic | Note |
|-------|------|
| Packaging as a library (`qasight`) | [Library Packaging](Library%20Packaging.md) |
| Database-agnostic storage | [Repository Pattern - Database-Agnostic Refactor](Repository%20Pattern%20-%20Database-Agnostic%20Refactor.md) |
| Any test runner as a stage | [Command Adapter and JUnit Import](Command%20Adapter%20and%20JUnit%20Import%20-%202026-09-25.md) |
| Run-to-run delta | [Phase 3 Delta Comparison Engine Plan](Phase%203%20Delta%20Comparison%20Engine%20Plan.md) → [Delta Comparison Policy](Delta%20Comparison%20Policy.md) |
| Unified dashboard | [Phase 3 Unified Dashboard Plan](Phase%203%20Unified%20Dashboard%20Plan.md) |
| BYOK AI failure summaries | [Phase 4 BYOK and Failure Analysis](Phase%204%20BYOK%20and%20Failure%20Analysis.md) |
| Cycles-first UI | [Phase 5 UI Redesign - Cycles-First Navigation](Phase%205%20UI%20Redesign%20-%20Cycles-First%20Navigation.md) |
| Navy glass look and sidebar | [UI Restyle - 2026-10-19](UI%20Restyle%20-%202026-10-19.md) |
| One engine, generated API types, blocking type checks | [Architecture Consolidation - 2026-10-06](Architecture%20Consolidation%20-%202026-10-06.md) |
| Architecture review: read-side scaling, migrations, layering enforcement (proposals only) | [Architecture Review - 2026-10-31](Architecture%20Review%20-%202026-10-31.md) |
| Release scorecard: the repo review tools on any repository or version, a 0-100 score per version, compared with the last one, and a release gate | [Release Scorecard - 2026-11-12](Release%20Scorecard%20-%202026-11-12.md) |
| Scorecard on MegaLinter: MegaLinter runs the linters for any language, scores follow SonarQube (worst security finding) and Codacy (issue density per 1,000 lines), new findings per version | [Scorecard on MegaLinter - 2026-11-12](Scorecard%20on%20MegaLinter%20-%202026-11-12.md) |
| Test labels and issue links, then environments, chosen-test runs and releases (gaps found next to Allure TestOps) | [Test Labels, Environments and Releases - 2026-11-02](Test%20Labels,%20Environments%20and%20Releases%20-%202026-11-02.md) |
| Better flaky signal: retries, same-commit results, flips per branch, broken kept apart | [Better Flaky Signal - 2026-11-01](Better%20Flaky%20Signal%20-%202026-11-01.md) |
| Importer and integration bug hunt: ranked findings, fixes and proposals | [Importer and Integration Bug Hunt - 2026-10-31](Importer%20and%20Integration%20Bug%20Hunt%20-%202026-10-31.md) |
| Engine fixes from the bug hunt: one run of a cycle at a time, and the other proposals | [Engine Fixes - 2026-11-01](Engine%20Fixes%20-%202026-11-01.md) |
| Interview-readiness batch (pull requests 67 to 91, v1.1.0) | [Interview Readiness Fixes - 2026-10-07](Interview%20Readiness%20Fixes%20-%202026-10-07.md) |
| Docs vault as second brain, wiki generated from `docs/` | [Docs Vault and Wiki Sync - 2026-10-07](Docs%20Vault%20and%20Wiki%20Sync%20-%202026-10-07.md) |
| Post-publish features: flaky tests, PR comments, plugins, adapters | [Open Source Features - 2026-10-19](Open%20Source%20Features%20-%202026-10-19.md) |
| Run summary and PR comment from the GitHub Action | [GitHub Action PR Comment - 2026-10-19](GitHub%20Action%20PR%20Comment%20-%202026-10-19.md) |
| Getting Started guide and the MkDocs docs site | [Docs Site - 2026-10-19](Docs%20Site%20-%202026-10-19.md) |
| Third-party frameworks and reporters through entry points | [Plugin API - 2026-10-19](Plugin%20API%20-%202026-10-19.md) |
| Playwright as its own equipment | [Playwright Adapter - 2026-10-19](Playwright%20Adapter%20-%202026-10-19.md) |
| Time to fix failing tests, suite and CI time, default-branch red time, and the waves of report metrics after them | [Test Delivery Metrics - 2026-11-02](Test%20Delivery%20Metrics%20-%202026-11-02.md) |
| Getting results out: copy menus, a manager report, PDF, PNG, CSV, XLSX and PowerPoint | [Exporting Results - 2026-11-01](Exporting%20Results%20-%202026-11-01.md) |
| Deleting chosen runs, stopping a run from the UI, and showing how long runs are kept | [Run Deletion and Stop - 2026-11-02](Run%20Deletion%20and%20Stop%20-%202026-11-02.md) |
| Quarantining flaky tests so they don't fail the run | [Flaky Test Quarantine - 2026-10-22](Flaky%20Test%20Quarantine%20-%202026-10-22.md) |
| Quarantine from the UI, API or `qasight quarantine`, kept on the server and applied to ingest and `pytest --qasight` too | [Quarantine From the UI - 2026-11-01](Quarantine%20From%20the%20UI%20-%202026-11-01.md) |
| Running only the tests a change can affect (`qasight affected`, `pytest --qasight-affected`) | [Affected Test Selection - 2026-10-30](Affected%20Test%20Selection%20-%202026-10-30.md) |
| Pools of keys (VMs, devices, accounts) leased to parallel workers | [Resource Pools and Leases - 2026-10-26](Resource%20Pools%20and%20Leases%20-%202026-10-26.md) |
| Checking keys, clean releases and a delay before reusing an expired key | [Resource Lifecycle Hooks - 2026-10-26](Resource%20Lifecycle%20Hooks%20-%202026-10-26.md) |
| A provider of your own commands that never creates or deletes VMs on its own | [Human-Run Resource Provider - 2026-10-30](Human-Run%20Resource%20Provider%20-%202026-10-30.md) |
| Playwright JSON reports: retries, flaky tests, traces | [Playwright JSON Import - 2026-10-22](Playwright%20JSON%20Import%20-%202026-10-22.md) |
| One-command setup for existing projects: `qasight init`, the zero-config Action, per-CI snippets, a PR comment preview | [Easy Setup - 2026-11-01](Easy%20Setup%20-%202026-11-01.md) |
| Reading Jest/Vitest, .NET, Go, Robot, RSpec, k6, XCTest, Android and Allure results in their own formats | [Runner Coverage - 2026-10-26](Runner%20Coverage%20-%202026-10-26.md) |
| Runs, stages and tests as OpenTelemetry traces (OTLP), off by default | [OpenTelemetry Export - 2026-10-30](OpenTelemetry%20Export%20-%202026-10-30.md) |
| The run history as an MCP server for coding agents (`qasight mcp`), read-only by default | [MCP Server - 2026-11-01](MCP%20Server%20-%202026-11-01.md) |
| Coverage on the PR comment, weekly digest, secrets hidden in stored results, backup and restore, failed-first ordering | [Free Copies of Paid Features - 2026-11-01](Free%20Copies%20of%20Paid%20Features%20-%202026-11-01.md) |
| The failing test's own source in AI failure summaries, opt-in and size-capped | [Test Source in AI Summaries - 2026-11-01](Test%20Source%20in%20AI%20Summaries%20-%202026-11-01.md) |
| Security and fresh-install review: findings R1 to R18, fixes and proposals | [Security and Install Review - 2026-10-31](Security%20and%20Install%20Review%20-%202026-10-31.md) |
| Slack and Teams alerts, failure annotations on the diff, flaky test issues | [Alerts Where Developers Look - 2026-10-22](Alerts%20Where%20Developers%20Look%20-%202026-10-22.md) |
| A GitHub issue per test that turns flaky, assigned from CODEOWNERS and closed once it is stable | [Flaky Test Issues - 2026-11-01](Flaky%20Test%20Issues%20-%202026-11-01.md) |
| BehaveX HTML report plus Allure results | [BehaveX Reports - 2026-10-21](BehaveX%20Reports%20-%202026-10-21.md) |
| JSON Schema for `qasight.yaml` (editor autocomplete) | [Config JSON Schema - 2026-10-22](Config%20JSON%20Schema%20-%202026-10-22.md) |
| Record results from any CI (`qasight ingest`) and a shared team server | [Ingest and Team Server - 2026-10-22](Ingest%20and%20Team%20Server%20-%202026-10-22.md) |
| Capped concurrent API runs and opt-in parallel stages | [Scale Runs and Stages - 2026-10-24](Scale%20Runs%20and%20Stages%20-%202026-10-24.md) |
| Optional persistent run queue and per-user accounts, off by default | [Switchable Team Features - 2026-10-24](Switchable%20Team%20Features%20-%202026-10-24.md) |
| Running each server-started stage in a throwaway container, off by default | [Throwaway Stage Containers - 2026-10-24](Throwaway%20Stage%20Containers%20-%202026-10-24.md) |
| Changelog enforcement | [changelog_automation_policy.md](../changelog_automation_policy.md) |
| Product roadmap from the product review (failing tests first, and what follows) | [Product Roadmap Work - 2026-10-25](Product%20Roadmap%20Work%20-%202026-10-25.md) |
| Product review second pass: every area except trust toward 10 | [Product to Ten - 2026-10-25](Product%20to%20Ten%20-%202026-10-25.md) |
| Project health page: a repeatable test health score and an optional AI review of the tests | [Project Health - 2026-10-26](Project%20Health%20-%202026-10-26.md) |
| README badge, asking about your tests, Jira issues from failures (and what was not built) | [Qualflare Follow-ups - 2026-10-26](Qualflare%20Follow-ups%20-%202026-10-26.md) |
| Naming stack overflows and crashes on failing stages and tests | [Crash Detection - 2026-10-31](Crash%20Detection%20-%202026-10-31.md) |
| UI design review: ranked design and UX findings with annotated screenshots, 7.5 / 10 | [UI Design Review - 2026-10-31](UI%20Design%20Review%20-%202026-10-31.md) |
| Engineering health review: tests, CI, docs accuracy and dependencies, fixes in #222 and ranked proposals | [Engineering Health Review - 2026-10-31](Engineering%20Health%20Review%20-%202026-10-31.md) |
| UI review fixes: cycle results, run labels, Compare test list, test page charts, quieter cards | [UI Review Fixes - 2026-11-01](UI%20Review%20Fixes%20-%202026-11-01.md) |
| Collapsible cards, 3-row lists, made-up examples in the live demo | [Tidy Cards and Demo Examples - 2026-11-05](Tidy%20Cards%20and%20Demo%20Examples%20-%202026-11-05.md) |
| Small touches from the Vision UI reference: light card edges, KPI icon tiles and health ring, sticky glass top bar, help card, footer; and its MIT licence | [UI Polish - 2026-11-08](UI%20Polish%20-%202026-11-08.md) |
| Automating the new user journey: setup pull request, fork comments, AI summaries in CI, automatic quarantine, MCP install, backups | [New User Journey Automation - 2026-11-01](New%20User%20Journey%20Automation%20-%202026-11-01.md) |
| Lifecycle fixes from the year-two review: storage limits and visibility, runs that die mid-way, token rotation | [Lifecycle Fixes - 2026-11-02](Lifecycle%20Fixes%20-%202026-11-02.md) |
| The rename from Testosterone (`testo-core`, `testo`) to QASight (`qasight`), and the old names that keep working | [Rename to QASight - 2026-11-04](Rename%20to%20QASight%20-%202026-11-04.md) |
| The QASight logo: the eye and the name, where its files come from and how they are rendered | [QASight Logo - 2026-11-05](QASight%20Logo%20-%202026-11-05.md) |
| External audit tools (gitleaks, pip-audit, Bandit, Semgrep, zizmor, Lighthouse): findings, the fixes, and the checks kept in CI | [External Audit Tools - 2026-11-11](External%20Audit%20Tools%20-%202026-11-11.md) |
| Release gate: fast third-party checks on every pull request, slow ones before every release, PyPI publish blocked without a pass | [Release Gate - 2026-11-12](Release%20Gate%20-%202026-11-12.md) |

Older plans and incident write-ups (Allure 3 migration plan, reporters port, report-links fix, CLI–UI parity, the `doctor`/`clean`/`watch`/`init` restore) are in the [Archive](../Archive/README.md).
