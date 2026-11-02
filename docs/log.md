---
type: log
status: current
created: 2026-10-07
updated: 2026-11-02
---

# Vault log

Append-only record of changes to the vault's shape. Rules are in the [vault schema](CLAUDE.md#log).

## 2026-06-25: vault created

Notes moved into `docs/` as an Obsidian vault, and copied once by hand to the GitHub wiki.

## 2026-10-07: restructure for GitHub

Wikilinks converted to relative markdown links, dated plans and checklists moved to `Archive/`, Index, Product Roadmap and Technical Debt Tracker rewritten (pull request 74).

## 2026-10-07: schema, lint and wiki sync

Added the [vault schema](CLAUDE.md), frontmatter on every note, `scripts/docs_vault.py lint` in CI, and a workflow that regenerates the GitHub wiki from `docs/`. The lint found four leftover wikilinks in Architecture Overview, Deep Dive - Execution Logic and GitLab Pages Demo; fixed. The wiki had not changed since 2026-06-24.

## 2026-10-07: decision notes and wiki-only page

Added [Architecture Consolidation - 2026-10-06](Specs%20&%20ADRs/Architecture%20Consolidation%20-%202026-10-06.md) and [Interview Readiness Fixes - 2026-10-07](Specs%20&%20ADRs/Interview%20Readiness%20Fixes%20-%202026-10-07.md). Moved the wiki-only page [GitHub Actions CI and AI Code Review](Archive/GitHub%20Actions%20CI%20and%20AI%20Code%20Review.md) into `Archive/` so the first wiki sync doesn't lose it.

## 2026-10-08: diagrams and gap fill

Added [System Diagram](Architecture/System%20Diagram.md): the architecture image was only embedded in the root README, so it never showed in Obsidian or the wiki. Architecture Overview's text sketch became a Mermaid flowchart. Added [Docs Vault and Wiki Sync - 2026-10-07](Specs%20&%20ADRs/Docs%20Vault%20and%20Wiki%20Sync%20-%202026-10-07.md), the second wave of the interview fixes, and this repo's workflows in CI-CD Pipeline Setup. Fixed two stale claims: 1.0.0 was never published to PyPI, and the PyPI trusted publisher named the old repository.

## 2026-10-19: setup prompts

Added [Setup Prompts](Prompts%20&%20Snippets/Setup%20Prompts.md), copy-paste prompts for adopting Testosterone in another project, linked from Index. Fixed a stale claim in Command Reference: `doctor`, `clean`, `watch` and `init` are registered commands, not roadmap items.

## 2026-10-19: getting started and docs site

Added [Getting Started](Getting%20Started.md), linked from Index, and [Docs Site - 2026-10-19](Specs%20&%20ADRs/Docs%20Site%20-%202026-10-19.md). The vault is now also built as a MkDocs site under `/docs/` of the Pages demo; the notes themselves did not change shape.

## 2026-10-19: post-publish features note

Added [Open Source Features - 2026-10-19](Specs%20&%20ADRs/Open%20Source%20Features%20-%202026-10-19.md), the decision record for the Tier 3 feature pull requests (flaky test detection first).

## 2026-10-19: GitHub Action PR comment note

Added [GitHub Action PR Comment - 2026-10-19](Specs%20&%20ADRs/GitHub%20Action%20PR%20Comment%20-%202026-10-19.md), linked from the Specs & ADRs README.

## 2026-10-19: plugin API

Added [Plugin API - 2026-10-19](Specs%20&%20ADRs/Plugin%20API%20-%202026-10-19.md) and a Plugins section in [Command Reference](CLI%20Commands/Command%20Reference.md#plugins).

## 2026-10-19: Playwright adapter

Added [Playwright Adapter - 2026-10-19](Specs%20&%20ADRs/Playwright%20Adapter%20-%202026-10-19.md).

## 2026-10-19: UI restyle

Added [UI Restyle - 2026-10-19](Specs%20&%20ADRs/UI%20Restyle%20-%202026-10-19.md) to Specs & ADRs and pointed the Phase 5 note at it for the sidebar and the navy tokens.

## 2026-10-21: BehaveX reports

Added [BehaveX Reports - 2026-10-21](Specs%20&%20ADRs/BehaveX%20Reports%20-%202026-10-21.md) to Specs & ADRs.

## 2026-10-22: flaky test quarantine

Added [Flaky Test Quarantine - 2026-10-22](Specs%20&%20ADRs/Flaky%20Test%20Quarantine%20-%202026-10-22.md) to Specs & ADRs.

## 2026-10-22: Ingest and team server

Added [Team Server](Processes%20&%20Guides/Team%20Server.md) to Processes & Guides and [Ingest and Team Server - 2026-10-22](Specs%20&%20ADRs/Ingest%20and%20Team%20Server%20-%202026-10-22.md) to Specs & ADRs, both linked from Index or their hub.

## 2026-10-22: Playwright JSON import

Added [Playwright JSON Import - 2026-10-22](Specs%20&%20ADRs/Playwright%20JSON%20Import%20-%202026-10-22.md) to Specs & ADRs.

## 2026-10-22: config JSON Schema

Added [Config JSON Schema - 2026-10-22](Specs%20&%20ADRs/Config%20JSON%20Schema%20-%202026-10-22.md) and an editor autocomplete section in [Command Reference](CLI%20Commands/Command%20Reference.md#editor-autocomplete).

## 2026-10-24: project review

Added [Project Review](project-review.md), a whole-repo scorecard and verdict, linked from [Index](Index.md).

## 2026-10-24: switchable team features

Added [Switchable Team Features - 2026-10-24](Specs%20&%20ADRs/Switchable%20Team%20Features%20-%202026-10-24.md) to Specs & ADRs (the `TESTO_RUN_QUEUE` and `TESTO_AUTH` switches).

## 2026-10-24: throwaway stage containers

Added [Throwaway Stage Containers - 2026-10-24](Specs%20&%20ADRs/Throwaway%20Stage%20Containers%20-%202026-10-24.md) to Specs & ADRs (the `TESTO_RUNNER` switch and the runner service).

## 2026-10-24: product review

Added [Product Review](product-review.md) next to the code-side Project Review, and pointed the [Product Roadmap](Roadmap%20&%20Strategy/Product%20Roadmap.md)'s next steps at its Now / Next / Later roadmap.

## 2026-10-25: product roadmap work

Added [Product Roadmap Work - 2026-10-25](Specs%20&%20ADRs/Product%20Roadmap%20Work%20-%202026-10-25.md) to Specs & ADRs (decisions behind each roadmap item from the Product Review).

## 2026-10-25: comparison page

Added [Compared With Other Tools](Compared%20With%20Other%20Tools.md), a user-facing guide next to Getting Started (in the docs site nav and the Index), from the Product Review's competitive position.

## 2026-10-26: resource pools

Added [Shared Resource Pools](Processes%20&%20Guides/Shared%20Resource%20Pools.md) (guide for `testo lease` and `resources:`) and [Resource Pools and Leases - 2026-10-26](Specs%20&%20ADRs/Resource%20Pools%20and%20Leases%20-%202026-10-26.md), linked from the Index, the Specs & ADRs README and the docs site menu.

## 2026-10-26: project health

Added [Project Health - 2026-10-26](Specs%20&%20ADRs/Project%20Health%20-%202026-10-26.md) to Specs & ADRs (the test health score and the test-only AI review checks).

## 2026-10-26: runner coverage

Added [Runner Coverage - 2026-10-26](Specs%20&%20ADRs/Runner%20Coverage%20-%202026-10-26.md) to Specs & ADRs (result files recognised by content: Jest/Vitest JSON, TRX, `go test -json`, Robot `output.xml`, Allure folders).

## 2026-10-26: resource lifecycle hooks

Added [Resource Lifecycle Hooks - 2026-10-26](Specs%20&%20ADRs/Resource%20Lifecycle%20Hooks%20-%202026-10-26.md) to Specs & ADRs (`on_check`, clean releases, `reuse_delay`), linked from its README and the docs site menu.

## 2026-10-26: qualflare follow-ups

Added [Qualflare Follow-ups - 2026-10-26](Specs%20&%20ADRs/Qualflare%20Follow-ups%20-%202026-10-26.md) to Specs & ADRs (README badge, asking about your tests, Jira issues from failures), linked from its README and the docs site menu.

## 2026-10-30: affected test selection

Added [Affected Test Selection - 2026-10-30](Specs%20&%20ADRs/Affected%20Test%20Selection%20-%202026-10-30.md) to Specs & ADRs (`testo affected`, `pytest --testo-affected`, the `affected:` section), linked from its README and the docs site menu.

## 2026-10-30: human-run resource provider

Added [Human-Run Resource Provider - 2026-10-30](Specs%20&%20ADRs/Human-Run%20Resource%20Provider%20-%202026-10-30.md) to Specs & ADRs (`provider.list`, `testo pool status`; Testosterone never creates or deletes keys on its own), linked from its README and the docs site menu.

## 2026-10-30: opentelemetry export

Added [OpenTelemetry Export - 2026-10-30](Specs%20&%20ADRs/OpenTelemetry%20Export%20-%202026-10-30.md) to Specs & ADRs (runs as OTLP traces, the `otel` reporter and `testo otel`), linked from its README and the docs site menu.

## 2026-10-31: architecture review

Added [Architecture Review - 2026-10-31](Specs%20&%20ADRs/Architecture%20Review%20-%202026-10-31.md) to Specs & ADRs (ranked findings on the read side, migrations and layering, with proposals), linked from its README, the Index and the docs site menu.

## 2026-10-31: crash detection

Added [Crash Detection - 2026-10-31](Specs%20&%20ADRs/Crash%20Detection%20-%202026-10-31.md) to Specs & ADRs (naming stack overflows and crashes on failing stages and tests), linked from its README and the docs site menu.
## 2026-10-31: feature gaps and quick wins

Added [Feature Gaps and Quick Wins - 2026-10-31](Roadmap%20&%20Strategy/Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md) to Roadmap & Strategy (competitor research and a new-user pass, ranked with effort), linked from the Index, the Product Roadmap and the docs site menu.

## 2026-10-31: ui design review

Added [UI Design Review - 2026-10-31](Specs%20&%20ADRs/UI%20Design%20Review%20-%202026-10-31.md) to Specs & ADRs with annotated screenshots under `assets/ui-review-2026-10-31/`, linked from its README, the Index and the docs site menu.

## 2026-10-31: engineering health review

Added [Engineering Health Review - 2026-10-31](Specs%20&%20ADRs/Engineering%20Health%20Review%20-%202026-10-31.md) to Specs & ADRs (tests, CI, docs accuracy, dependencies), linked from its README, the Index and the docs site menu.
## 2026-10-31: engine and CLI bug hunt

Added [Engine and CLI Bug Hunt](engine-cli-review.md) (reproduced bugs, fixes, ranked proposals), linked from the [Index](Index.md) and the docs site menu.

## 2026-10-31: importer and integration bug hunt

Added [Importer and Integration Bug Hunt - 2026-10-31](Specs%20&%20ADRs/Importer%20and%20Integration%20Bug%20Hunt%20-%202026-10-31.md) to Specs & ADRs (ranked findings, fixes and proposals), linked from its README, the Index and the docs site menu.

## 2026-10-31: paid features to offer free

Added [Paid Features to Offer Free - 2026-10-31](Roadmap%20&%20Strategy/Paid%20Features%20to%20Offer%20Free%20-%202026-10-31.md) to Roadmap & Strategy (competitor paywalls, twelve ranked ideas, licence and trademark rules), linked from the Index, the Product Roadmap and the docs site menu.

## 2026-10-31: security and install review

Added [Security and Install Review - 2026-10-31](Specs%20&%20ADRs/Security%20and%20Install%20Review%20-%202026-10-31.md) to Specs & ADRs (findings R1 to R18, what was fixed, proposals P1 to P4), linked from its README and the docs site menu.
## 2026-11-01: UI review fixes

Added [UI Review Fixes - 2026-11-01](Specs%20&%20ADRs/UI%20Review%20Fixes%20-%202026-11-01.md) to Specs & ADRs (the five proposals built from the UI design review), linked from its README, the Index and the docs site menu.

## 2026-11-01: engine fixes

Added [Engine Fixes - 2026-11-01](Specs%20&%20ADRs/Engine%20Fixes%20-%202026-11-01.md) to Specs & ADRs (what each approved bug-hunt proposal changed), linked from its README and the docs site menu.

## 2026-11-01: better flaky signal

Added [Better Flaky Signal - 2026-11-01](Specs%20&%20ADRs/Better%20Flaky%20Signal%20-%202026-11-01.md) to Specs & ADRs (gap G1), linked from its README, the Index and the docs site menu.

## 2026-11-01: flaky test issues

Added [Flaky Test Issues - 2026-11-01](Specs%20&%20ADRs/Flaky%20Test%20Issues%20-%202026-11-01.md) to Specs & ADRs (`TESTO_ISSUES=github`, `testo issues sync`), linked from its README and the docs site menu.

## 2026-11-01: MCP server

Added [MCP Server - 2026-11-01](Specs%20&%20ADRs/MCP%20Server%20-%202026-11-01.md) to Specs & ADRs (`testo mcp`), linked from its README and the docs site menu.

## 2026-11-01: quarantine from the UI

Added [Quarantine From the UI - 2026-11-01](Specs%20&%20ADRs/Quarantine%20From%20the%20UI%20-%202026-11-01.md) to Specs & ADRs (gap G2, quick win Q8), linked from its README, the Index and the docs site menu; [Flaky Test Quarantine - 2026-10-22](Specs%20&%20ADRs/Flaky%20Test%20Quarantine%20-%202026-10-22.md) now points to it.
## 2026-11-01: easy setup

Added [Easy Setup - 2026-11-01](Specs%20&%20ADRs/Easy%20Setup%20-%202026-11-01.md) to Specs & ADRs (`testo init` that detects the stack, and the follow-ups for the Action, CI snippets and a PR comment preview), linked from its README and the docs site menu.

## 2026-11-01: setup snippets

Added [Setup Snippets](Processes%20&%20Guides/Setup%20Snippets.md) to Processes & Guides (generated copy-paste CI setup per runner and CI system), linked from the Index, Getting Started, CI-CD Pipeline Setup and the docs site menu.

## 2026-11-01: free copies of paid features

Added [Free Copies of Paid Features - 2026-11-01](Specs%20&%20ADRs/Free%20Copies%20of%20Paid%20Features%20-%202026-11-01.md) to Specs & ADRs (coverage on the PR comment, weekly digest, redaction before storing, backup and restore, failed-first ordering), linked from its README and the docs site menu.

## 2026-11-01: new user journey audit

Added [New User Journey Audit - 2026-11-01](Roadmap%20&%20Strategy/New%20User%20Journey%20Audit%20-%202026-11-01.md) to Roadmap & Strategy (manual steps, automation and ranked proposals for each step of a new user's path), linked from the Index, the Product Roadmap and the docs site menu.

## 2026-11-01: new user journey automation

New ADR [New User Journey Automation - 2026-11-01](Specs%20&%20ADRs/New%20User%20Journey%20Automation%20-%202026-11-01.md), linked from the Specs README, the audit's Status section and the docs site menu (#278).

## 2026-11-01: test source in AI summaries

New ADR [Test Source in AI Summaries - 2026-11-01](Specs%20&%20ADRs/Test%20Source%20in%20AI%20Summaries%20-%202026-11-01.md) (opt-in failing test source in the AI failure summary), linked from the Specs README, Phase 4 BYOK and Failure Analysis, the Command Reference and the docs site menu.
