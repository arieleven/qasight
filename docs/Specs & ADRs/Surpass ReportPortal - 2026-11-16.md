---
type: spec
status: current
created: 2026-11-16
updated: 2026-11-16
---

# Surpass ReportPortal - 2026-11-16

## Context

Taltal asked whether a month with beta teams would bring QASight to ReportPortal's level, and what it would take to pass it. The research (shared as a Hebrew document) found that QASight already leads on setup (one container against ReportPortal's eight-plus services), running the tests, diffs between runs, flaky handling and CLI and agent use, and trails on five things a ReportPortal team would miss: its agents (teams report through them and won't rewrite their setup), years of history and defect decisions, auto-analysis that learns from those decisions, proof that it holds a large organisation's volume, and per-project access and a bug tracker link. A beta month by itself fixes none of these; it only shows them. The research ranked six moves, and Taltal approved all six.

## Decision

1. **A ReportPortal-compatible reporting endpoint.** `/api/v1/{project}/…` and `/api/v2/{project}/…` answer the calls agents make (launch, item, nested item, finish, log with attachments, finish launch, `GET /api/info`), in `qasight/services/rp_compat.py` and `qasight_api/routes/rp_compat.py`. What a launch reports is kept in three tables (`rp_launch`, `rp_item`, `rp_log`) until it finishes, then turned into Allure result files and recorded through `ingest_report_files`, the same `CycleRunService` path as `POST /api/v1/ingest`: one engine, no second write path. Each finished test also goes to the live "Running now" list. Tested with the real `pytest-reportportal` 5.6 and `@reportportal/agent-js-playwright` 5.5 agents.
2. **Import of ReportPortal history.** `qasight import reportportal` (`qasight/services/rp_import.py`) reads launches through ReportPortal's API, records each as a run dated when it ran, and turns defect types into marked causes, with bug-tracker links in the note. Launches are remembered by `reportportal_launch_uuid` in the run's metadata, so the import can run again.
3. **Cause suggestions that learn.** `qasight/services/similar_causes.py` compares a new failure's message with the failures the team marked (TF-IDF over words, word pairs and four-letter word pieces, cosine similarity, the closest five vote, other cycles count 0.8). It comes before the fixed rules, and the run page says how sure it is and which marked failure it looks like. `qasight triage accuracy` and `GET /api/v1/triage/accuracy` replay every marked cause, leaving it out, to measure precision and coverage on a team's own data. No model is trained or stored: the marked causes are the model, so a mark takes effect at once and every suggestion can be explained.
4. **Scale, measured.** `TESTO_WORKERS` runs several API worker processes. Live sessions moved from memory to the database (`live_session`, `qasight/services/live_sessions.py`, because `qasight` may not import `qasight_api`), so every worker sees them. SQLite gets a 30-second busy timeout and write-ahead mode with several workers. `scripts/benchmark.py` stores a million results and times agents and pages; the numbers are in [Benchmark](../Processes%20&%20Guides/Benchmark.md).
5. **Roles per cycle and Jira.** `qasight users grant NAME ROLE PATTERN` (`qasight/project_roles.py`, table `project_role`) gives a role on the cycles matching a pattern; the most specific pattern wins. The security guard lets a write through when the path names a cycle the account may change, and each route checks the cycle in the body with `require_cycle_role`. `POST /api/v1/jira/issues` (`qasight/services/jira_issues.py`) files a failing test in Jira Cloud or Data Center and marks its cause as a product bug with the issue link.
6. **Case studies.** `qasight case-study` prints before and after numbers for a team's cycles around its switch day, and the [Beta Case Study Kit](../Processes%20&%20Guides/Beta%20Case%20Study%20Kit.md) has the interview questions and the template.

Migration `0009` adds the five tables.

## Consequences

- A ReportPortal team can switch by changing an endpoint and a key, with its history and decisions imported: the step-by-step is [Moving from ReportPortal](../Processes%20&%20Guides/Moving%20from%20ReportPortal.md).
- ReportPortal's own UI API (dashboards, filters, defect edits, merges) is not answered; agents only report.
- Roles per cycle cover writes. Reads stay server-wide, and accounts are browser sign-ins, so CI and agents still use the admin token. Per-project tokens would be the next step if a beta team needs them.
- Several workers share uploads, reads and agents, but a run started from the UI lives in the worker that took the request, so the server still runs one replica; `TESTO_RUN_QUEUE=db` keeps queued runs shared.
- Jira issues are filed and linked; their status is not read back, and flaky tests are not synced to Jira (they are to GitHub issues).
- The pytest agent sends its logs at the end of a test, so the live list can show a failure before its message; the recorded run has it.
- At a million stored results the run failures, flaky and test search pages take 2 to 3 seconds (they read the cycle's recent result files). They are the next thing to speed up; the runs list and dashboard stay near 10 ms.
- The vault gains [Moving from ReportPortal](../Processes%20&%20Guides/Moving%20from%20ReportPortal.md), [Benchmark](../Processes%20&%20Guides/Benchmark.md) and [Beta Case Study Kit](../Processes%20&%20Guides/Beta%20Case%20Study%20Kit.md); [Team Server](../Processes%20&%20Guides/Team%20Server.md), [Command Reference](../CLI%20Commands/Command%20Reference.md) and [Compared With Other Tools](../Compared%20With%20Other%20Tools.md) are updated.
