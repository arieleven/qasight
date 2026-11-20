---
type: spec
status: current
created: 2026-11-20
updated: 2026-11-20
---

# Focus Review - 2026-11-20

## Context

By 1.7 QASight had about 40 commands, four ways in (CLI, web UI, REST API, MCP) and two promises side by side: track your tests, and grade your code per release. Taltal worried it read as "AI slop". The features were real and tested; what made it look scattered was that everything sat at the same height. `qasight --help` listed 37 commands in five panels, 20 of them under "Run and report"; the README's "What's new" listed about 30 features in one paragraph; the Overview showed two scores and seven cards.

QASight is CLI-first and mostly driven by coding agents (see [CLI-Only Bug Hunt - 2026-11-15](CLI-Only%20Bug%20Hunt%20-%202026-11-15.md)), and Taltal values versatility, so the rule was to hide and make optional rather than delete.

## Decision

**One promise:** "Every test run, remembered. After a red build, know in a minute what's new, what's flaky and what got slower, from your terminal, your CI or your coding agent." It leads the README, `qasight --help` and the Overview.

**Three rings.** Core is what answers the promise and is shown first. Optional stays in the product, documented, and off or out of sight until it is set up. Removal is rare and goes through the [Stability and Deprecation Policy](../Processes%20&%20Guides/Stability%20and%20Deprecation%20Policy.md).

| Surface | Change |
|---------|--------|
| CLI | `qasight --help` shows ten commands in Start (`init`, `demo`, `doctor`), Record (`run`, `ingest`), Understand (`summary`, `flaky`, `ui`) and Share (`ci`, `mcp`). The rest are hidden from that page only: `qasight help --all` lists them under "More:" panels, and `qasight help --json` describes every command, alias and exit code for agents. `try`, `pool` and `config-db` become `ci preview`, `lease pools` and `config db`; the old names stay as aliases (not deprecated). `version` is unlisted (`--version`). See [Command Reference](../CLI%20Commands/Command%20Reference.md). |
| README | 395 lines to about 110: the promise, one screenshot, three commands to start, a table of the three questions, the Action, and one table of optional features. The API table moved to the [Architecture Overview](../Architecture/Architecture%20Overview.md#rest-api), local development to `CONTRIBUTING.md`, the v1.0 migration to the deprecation policy, release notes stay in `CHANGELOG.md`. |
| Overview | Needs attention and the latest run open the page; Test health is the one score, Code quality appears once a scorecard has run. Delivery numbers stay on the Report page. Ask shows in the menu once an AI provider is set up, Resources once a pool exists (both still reachable by URL). Since [Merge and Automate](Merge%20and%20Automate%20-%202026-11-20.md), Resources is a tab of Settings and Test health opens inside the Overview. |
| Removal | The InfluxDB and Prometheus Pushgateway push is deprecated (OpenTelemetry covers it) and goes in 2.0. |

The code quality scorecard and release gate are an optional module, not half of the identity: they keep every feature, and leave the first screen and the main help.

Not done here: PR #340 was open during this review. Its `qasight case-study` and `triage accuracy` commands are development tools and belong in `scripts/` or under `help --all`, not in the core help; that is decided on #340 itself. `export-site` and `badge` stay top-level commands under "More: reports and alerts" instead of moving under `export`, which would change how `export` parses its arguments.

## Consequences

- A new command or page has to say which ring it belongs to. Only something that helps answer "what's new, what's flaky, what got slower" goes in the core help, the README's first half or the Overview.
- Hidden commands are not offered by shell completion; they run exactly as before, and `qasight help --all` lists them.
- Scripts and agents that read `qasight --help` see fewer commands; `qasight help --json` is the full, stable list.
- The review itself (in Hebrew) is in the project's files, `focus-review/qasight-focus-review-2026-11-19.md`.
