---
type: spec
status: current
created: 2026-11-20
updated: 2026-11-20
---

# Closed-Network Monorepo Rollout - 2026-11-20

[Index](../Index.md) · [Specs & ADRs](README.md) · [Closed Network Install](../Processes%20&%20Guides/Closed%20Network%20Install.md) · [Closed Network Install - 2026-11-15](Closed%20Network%20Install%20-%202026-11-15.md)

## Context

Putting QASight into a large monorepo on a closed network in about a week: self-hosted GitLab CI, pytest, Behave and BehaveX, many teams' jobs already in place, a local model that can't carry heavy work, and no internet or Claude on site. The goal was the least work per team. Trying it on a small monorepo showed what got in the way:

- `qasight init --scan` found pytest but not Behave or BehaveX, though QASight runs both natively.
- Its GitLab file gave two folders with the same runner the same job name (`pytest:`), so one replaced the other, and the artifacts path missed the folder.
- Finding result files stopped after 20,000 files of any kind, so in a big checkout it could stop before reaching the result folders.
- A GitLab job gets earlier stages' artifacts; recording whatever it found would record those tests again under the later job's name.
- Behave and BehaveX write one JUnit file per feature, so a suite with more than 200 features failed the server's upload limit.
- The team server ignored `TESTO_AI_*` in its environment (only the CLI read them), so a local model set in `.env` as the guide said left AI off.

## Decision

- **One include for the whole monorepo:** `ci/gitlab/qasight-every-job.gitlab-ci.yml` sets a `default:` `after_script` that records any job's results on the team server, named after the job, and does nothing for jobs without results. No team edits its jobs beyond making the runner write JUnit (`--junitxml`, `--junit`).
- `init --scan` detects Behave and BehaveX (features folder with `steps/`, plus the dependency or `from behave`), always naming the features folder; CI jobs take the stage's name when a runner repeats.
- The result search counts only files that could be results.
- In GitLab, auto-found files older than `CI_JOB_STARTED_AT` (30 s slack) are left out.
- `qasight ingest --server` packs JUnit XML into a few `<testsuites>` files when there are more than 200; the server and its limits are unchanged.
- The server builds its AI settings the way the CLI does: `TESTO_AI_PROVIDER` and friends win over the Settings file; an unknown provider is logged and ignored.

## Consequences

- A team joins by having its runner write JUnit; the server shows every job as a cycle from the first pipeline.
- Jobs with their own `after_script` need one `!reference` line.
- Images need Python 3.11+ for `qasight`; older ones use the `qasight-upload` binary, which sends globs and doesn't yet leave out earlier artifacts.
- `qasight ingest --from-gitlab` (added the same day, after Taltal chose it): reads each pipeline's test report (`/pipelines/:id/test_report`, plus child pipelines) through GitLab's API and records each job as a cycle, dated when it ran. Jobs that already declare `artifacts: reports: junit:` need no change, and the first pass brings history from past pipelines. It records into the machine's own history (run it in the server's container), because backdating a run isn't part of the upload API.
