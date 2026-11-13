---
type: spec
status: current
created: 2026-11-12
updated: 2026-11-12
related: "Easy Setup, New User Journey Automation, Adoption Blockers"
---

# Plug and Play - 2026-11-12

[Index](../Index.md) · [Specs & ADRs](README.md) · [Easy Setup](Easy%20Setup%20-%202026-11-01.md)

## Context

[Easy Setup](Easy%20Setup%20-%202026-11-01.md) gave `qasight init` (detect runners, write the config and a workflow, record a first run), and [New User Journey Automation](New%20User%20Journey%20Automation%20-%202026-11-01.md) automated the rest of the first day. On 2026-11-12 the question was what still stops a team from adding QASight in minutes without reading anything. Seven gaps were found, ranked by how much setup friction they remove:

1. `init` writes a second workflow that runs the tests again next to the one that already runs them (twice the CI minutes, two files to keep in step).
2. Recording a run needs a config file or a result-file flag the user has to know.
3. Setup needs a terminal with Python.
4. Flaky and slower-test detection needs weeks of history before it says anything.
5. JavaScript, Go and Java teams need `uv` or `pipx` to run the CLI.
6. In a monorepo every pull request runs every package's tests.
7. Cypress is detected but left out because it needs the app running.

All seven were approved, built in three waves, one pull request each: 1 and 2, then 3, 4 and 6, then 5 and 7.

## Decision

- **Attach to the CI a project already has** (`qasight init --attach`, `qasight/services/ci_attach.py`; reference: [Command Reference § qasight init](../CLI%20Commands/Command%20Reference.md#qasight-init)). The GitHub workflows are edited as text, guided by PyYAML node positions (`yaml.compose`), so nothing but the touched lines changes: a result-file flag on each known test command, one QASight step per job after its last test step, with `results:` so the Action records those files (its existing ingest mode) instead of running a cycle, and `pull-requests: write` added to a permissions block that lacks it. A command only gets a flag when it starts a shell command and nothing follows it on its line; anything else (chained commands, folded scalars, Playwright and `go test`, which need a reporter or a redirect) is listed for the user instead of guessed. It stays opt-in for now: editing someone's pipeline is shown as a diff with `--dry-run`, and plain `init` points at `--attach` when it sees test steps. Only GitHub Actions; GitLab and the others keep the side file `init` writes today.

- **Any test command, no config** (`qasight run -- <command>`, `qasight/services/wrap_run.py`; reference: [Command Reference § Any test command, no config](../CLI%20Commands/Command%20Reference.md#any-test-command-no-config)). The command is recognised (directly, through `npx`/`uv run`/`python -m`, or through the npm, pnpm or yarn script it names), gets the flag that writes a result file into `.testo/wrap/<stamp>/`, runs with its own output and exit code, and the files are recorded through the same ingest path as `qasight ingest`. An unknown command is recorded from the result files it wrote while it ran, found by content.

- **Setup from the GitHub website** (wave 2). A one-time workflow, `qasight-setup.yml` (`ci_templates.setup_workflow()`), runs the Action's new `setup` mode when it is committed: `qasight init --scan --yes --attach --no-run --ci github --pr` as github-actions[bot], so the scan, the attach and the pull request are the same code as on a laptop. Getting Started shows the file and a button that opens GitHub's new-file editor with it filled in (`/new/<branch>?filename=...&value=...`); the button reads the code block on the page, and a test keeps that block equal to the template. A pull request opened with Actions' token doesn't start other workflows and some repositories forbid it, so the docs say to push a commit to it, and `init --pr` prints the compare link when only the pull request is missing.

The later waves add their decisions here as they land.

## Consequences

- A team with CI keeps one workflow; QASight costs one extra step, not a second test run.
- The flags write files named `qasight-*` in the step's working directory, so they don't clash with result files a project already writes.
- A test command QASight doesn't know, or one it can't extend safely, isn't recorded until the user adds a reporter; the output says which.
