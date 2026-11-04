---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-04
related: "New User Journey Audit - 2026-11-01, Easy Setup - 2026-11-01, Quarantine From the UI - 2026-11-01, MCP Server - 2026-11-01"
---

# New User Journey Automation - 2026-11-01

[Index](../Index.md) · builds the proposals of the [New User Journey Audit](../Roadmap%20&%20Strategy/New%20User%20Journey%20Audit%20-%202026-11-01.md).

## Context

The audit walked the 12 steps a new user takes, from finding QASight to running it for a team, and ranked what could be automated at each one. Taltal asked for all of them on 2026-11-01 ("do all of them"). The [audit's Status section](../Roadmap%20&%20Strategy/New%20User%20Journey%20Audit%20-%202026-11-01.md#status) tracks which pull request built which proposal.

## Decision

One pull request per few proposals, each on the existing paths: setup stays in `qasight init`, the comment in `qasight report markdown` and the Action wrapper, quarantine in `quarantine_store` and `CycleRunService`. New behaviour that changes what a run does is off by default and switched on by one environment variable or flag.

- **Setup to a pull request** (`qasight init --pr`, `--dependabot`, `--server`, `--forks`): only git and `gh` as child processes with fixed argv, so sign-in stays `gh`'s job. Only the files init wrote are committed. A secret goes to `gh secret set` on stdin, never argv or output.
- **One test, two recording methods** (V1): the diff pairs a key seen on one side with a key on the other whose name folds to the same identity, only when exactly one test on each side has it. Flaky history still keys on the stored key, so the comment asks for one method per cycle.
- **AI summaries outside the UI** (`qasight ai`, `report markdown --ai`, `ai-summary`): the CLI reads and writes the UI's settings file; `TESTO_AI_PROVIDER` wins in CI. Key values are never saved. Model output goes in the comment as an escaped quote.
- **Comments on fork pull requests** (`comment-artifact`, `post-comments-from`): the fork's run uploads the Markdown; a `workflow_run` job in the base repository posts it. That job checks out nothing, accepts only files with the comment marker under a size cap, defuses mentions, and finds the pull request from the tested commit, never from the artifact.
- **Team server from CI** (`server-url`): the local cached history still feeds the comment; the upload is a second, best-effort step.
- **Automatic quarantine** (`TESTO_QUARANTINE_AUTO`): after each recorded run, a test flaky at the rate *and failing in that run* is added; auto-release (from #249) takes it out after clean runs, and it only comes back if it fails again. Escaped names let parametrized tests take part.
- **`qasight mcp install`**: project files get relative paths so they can be committed; Claude Desktop's per-machine file gets absolute ones.
- **Backups**: a compose service, not a server thread, so the API process stays the same and the service can be scaled to zero.
- **CI history on a laptop** (`qasight ui --from-github`): the Action uploads only the SQLite file as an artifact (kept 7 days, `history-artifact: false` turns it off); the CLI finds it with `gh api` and downloads it with `gh run download`, into a folder of its own with its own `TESTO_HOME`, so it never mixes with local runs. Reports and attachments stay in CI.
- **Update notice**: one PyPI JSON request a day at most, the result (failures too) cached in `~/.cache/qasight/`, off in CI and with `TESTO_NO_UPDATE_CHECK=1`. The UI reads it from `GET /api/v1/version`, which sits behind read protection (not under `/health/`) so the version isn't public.
- **Missing extras**: the hint comes from the installed package's own metadata, so it can't drift from `pyproject.toml`; a module qasight doesn't need keeps its traceback.
- **`qasight server init`**: the packaged compose file is the repository's (a test keeps them equal); secrets go only to a `.env` created with mode 600. The first admin is created at start only while no account exists, so `.env` can never reset a password.
- **`qasight plugins new`**: templates ship in the package as `.tmpl` files; the output is `examples/plugin` under the new name, and a test runs the written runner.

## Consequences

- Every new switch is documented on the note that owns it ([Command Reference](../CLI%20Commands/Command%20Reference.md), [CI-CD Pipeline Setup](../Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md), [Team Server](../Processes%20&%20Guides/Team%20Server.md)) and in `CHANGELOG.md`.
- Fork comments need a second workflow file; `qasight init --forks` writes it, the zero-config path doesn't.
- Automatic quarantine can hide a real regression that happens to flip; it is off by default, and the comment names every test it adds.
- Backups live on the same volume as the data until someone copies them off; the Team Server note says so.
