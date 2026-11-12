---
type: index
status: current
created: 2026-06-25
updated: 2026-11-12
---

# QASight docs

Design notes for QASight (`qasight`, CLI `qasight`).
New here? [Getting Started](Getting%20Started.md) adds it to your project in about five minutes. For the design, start with [ARCHITECTURE.md](../ARCHITECTURE.md) and [README.md](../README.md).

The notes are plain markdown. They open as an Obsidian vault, and are published as a searchable site at <https://arieleven.github.io/qasight/docs/>. Dated plans, audits and phase checklists from earlier development are in the [Archive](Archive/README.md).

## Architecture

- [System Diagram](Architecture/System%20Diagram.md): the architecture picture, what each part does, and where every other diagram lives
- [Architecture Overview](Architecture/Architecture%20Overview.md): component flowchart, modules, engine, adapters, artifact layout, persistence
- [Deep Dive - Execution Logic](Architecture/Deep%20Dive%20-%20Execution%20Logic.md): session init, the subprocess loop, teardown

## Using the CLI

- [Getting Started](Getting%20Started.md): install, write `qasight.yaml`, run a cycle, see what changed, run it in GitHub Actions
- [Compared With Other Tools](Compared%20With%20Other%20Tools.md): where QASight fits next to Allure, ReportPortal, Currents, Trunk and Datadog, and when to pick one of them instead
- [Setup Prompts](Prompts%20&%20Snippets/Setup%20Prompts.md): copy-paste prompts for an AI assistant to install QASight in your project, write `qasight.yaml`, add CI and open the UI
- [Command Reference](CLI%20Commands/Command%20Reference.md): every `qasight` command, flag and exit code, and the `qasight.yaml` schema
- [Troubleshooting and Error Codes](CLI%20Commands/Troubleshooting%20and%20Error%20Codes.md): exit codes, NDJSON errors, debugging playbook

## Testing and CI

- [QA Strategies](Testing%20Workflows/QA%20Strategies.md): how runs are defined, triggered, logged, and how the orchestrator tests itself
- [Setup Snippets](Processes%20&%20Guides/Setup%20Snippets.md): copy-paste CI setup for each runner (pytest, Jest, Vitest, Playwright, Go, .NET, Maven, Gradle, RSpec, PHPUnit) on GitHub Actions, GitLab CI, Jenkins and CircleCI, no config file needed
- [CI-CD Pipeline Setup](Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md): GitHub Action, GitLab template, runner image, test tiers
- [Team Server](Processes%20&%20Guides/Team%20Server.md): one shared history for every CI job, with `qasight ingest --server`, the UI and Postgres in Docker
- [Shared Resource Pools](Processes%20&%20Guides/Shared%20Resource%20Pools.md): give each parallel worker its own VM, device or test account with `qasight lease`, safely across laptops
- [E2E Harness Operations Guide](Processes%20&%20Guides/E2E%20Harness%20Operations%20Guide.md): the external E2E harness
- [GitLab Pages Demo](Processes%20&%20Guides/GitLab%20Pages%20Demo.md): the GitHub and GitLab Pages demo pipeline

## Releasing

- [Publishing to PyPI](Processes%20&%20Guides/Publishing%20to%20PyPI.md) · [Publishing Docker Images](Processes%20&%20Guides/Publishing%20Docker%20Images.md) · [Publishing to Artifactory](Processes%20&%20Guides/Publishing%20to%20Artifactory.md)
- [Changelog policy](changelog_automation_policy.md)

## Decisions and direction

- [Specs & ADRs](Specs%20&%20ADRs/README.md): design decisions that still describe the code
- [Product Roadmap](Roadmap%20&%20Strategy/Product%20Roadmap.md): current state and next steps
- [Technical Debt Tracker](Testing%20Workflows/Technical%20Debt%20Tracker.md): open backlog
- [Project Review](project-review.md): whole-repo scorecard (architecture, code, tests, security, docs, CI) and verdict, 2026-10-24
- [Architecture Review](Specs%20&%20ADRs/Architecture%20Review%20-%202026-10-31.md): ranked review of module boundaries, data model, scaling limits and extension points, with proposed next steps, 2026-10-31
- [Release Scorecard](Specs%20&%20ADRs/Release%20Scorecard%20-%202026-11-12.md): run the repo review tools (security, dependencies, API, web, load, tests) on any repository or version, score it 0-100, compare it with the last version and gate the release, 2026-11-12
- [Scorecard on MegaLinter](Specs%20&%20ADRs/Scorecard%20on%20MegaLinter%20-%202026-11-12.md): the scorecard runs MegaLinter on any repository and scores like SonarQube and Codacy, 2026-11-12
- [Better Flaky Signal](Specs%20&%20ADRs/Better%20Flaky%20Signal%20-%202026-11-01.md): flaky means passed on retry, mixed results on one commit, or flips on one branch; broken tests kept apart, 2026-11-01
- [Quarantine From the UI](Specs%20&%20ADRs/Quarantine%20From%20the%20UI%20-%202026-11-01.md): a Quarantine button and `qasight quarantine`, a list the server keeps, applied to `qasight run`, `qasight ingest` and `pytest --qasight`, released after clean runs, 2026-11-01
- [Importer and Integration Bug Hunt](Specs%20&%20ADRs/Importer%20and%20Integration%20Bug%20Hunt%20-%202026-10-31.md): ranked bugs found by feeding the importers malformed and real-tool reports, and in the Action, PR comment, alerts, OTel, affected selection and plugins, with fixes and proposals, 2026-10-31
- [Product Review](product-review.md): product scorecard (positioning against Allure, ReportPortal, Currents and Trunk, onboarding, the red-build workflow, design, accessibility, copy) and the Now / Next / Later roadmap, 2026-10-24
- [Feature Gaps and Quick Wins](Roadmap%20&%20Strategy/Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md): what paid flaky-test and test-analytics tools do that QASight doesn't yet, ranked with effort, and the bugs a new user hits in the first ten minutes, 2026-10-31
- [New User Journey Audit](Roadmap%20&%20Strategy/New%20User%20Journey%20Audit%20-%202026-11-01.md): every step from finding QASight to running it for a team, what is still done by hand, and ranked proposals to automate it, 2026-11-01
- [Paid Features to Offer Free](Roadmap%20&%20Strategy/Paid%20Features%20to%20Offer%20Free%20-%202026-10-31.md): which features competitors keep behind paid plans, the ones QASight could give away self-hosted, ranked, and the licence and trademark rules for copying ideas, 2026-10-31
- [UI Design Review](Specs%20&%20ADRs/UI%20Design%20Review%20-%202026-10-31.md): the web UI as a piece of design (hierarchy, consistency, copy, charts, states, accessibility), ranked findings with annotated screenshots, 2026-10-31
- [Engineering Health Review](Specs%20&%20ADRs/Engineering%20Health%20Review%20-%202026-10-31.md): test suite, CI, docs accuracy and dependencies (flaky tests, Dependabot, TypeScript 7), what was fixed and ranked proposals, 2026-10-31
- [UI Review Fixes](Specs%20&%20ADRs/UI%20Review%20Fixes%20-%202026-11-01.md): what was built from the review's proposals (cycle results on the Cycles grid, run labels with time and branch, the Compare test list under the summary, test page charts, quieter cards), 2026-11-01
- [Engine and CLI Bug Hunt](engine-cli-review.md): reproduced bugs in `qasight`, config parsing, the engine, storage and the importers, what was fixed, and ranked proposals for the rest, 2026-10-31
- [Agent Context Guide](Prompts%20&%20Snippets/Agent%20Context%20Guide.md): which note to read before changing what (for AI coding agents)

## The vault itself

- [Vault schema](CLAUDE.md): frontmatter, linking and the ingest / query / lint rules for these notes
- [Vault log](log.md): record of changes to the vault's structure
