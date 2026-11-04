---
type: roadmap
status: current
created: 2026-11-01
updated: 2026-11-04
---

# New user journey audit (2026-11-01)

[Index](../Index.md) · [Product Roadmap](Product%20Roadmap.md) · [Getting Started](../Getting%20Started.md) · [Easy Setup - 2026-11-01](../Specs%20&%20ADRs/Easy%20Setup%20-%202026-11-01.md) · [Feature Gaps and Quick Wins](Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md)

Every step a new user takes, from finding QASight to running it for a team, checked against the code on `main` at `04246b5` (after #273). For each step: what the user still does by hand, what the tool already does for them, where it hurts, and what could be automated. **Nothing here is built.** The proposals wait on the maintainer's pick.

How it was checked: reading the CLI commands, the Action, the API routes, the UI's empty states and the docs, plus a run of the pytest path in a fresh folder (`qasight init`, `qasight try`, `qasight doctor`). Work still in open pull requests is named where it changes the answer: #249 (stricter flaky detection, quarantine from the UI, flaky-test issues, `qasight mcp`), #248 (`qasight backup` / `qasight restore` and other paid-tier copies) and `qasight init --scan` (in progress in another thread).

Effort, as in [Feature Gaps and Quick Wins](Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md): **S** under a day, **M** one to three days, **L** one to two weeks. Impact: **High** most new users hit it, **Med** many do, **Low** a few.

## The top five gaps

| # | Gap | Step | Impact | Effort |
|:-:|---|:-:|:-:|:-:|
| 1 | **`qasight init` is the easiest way in, but almost nothing points at it.** The README's "Try it in a minute" and Quickstart, the UI's first-run guide (which says `qasight config init`), the `/try` page and the Setup Prompts all lead with older paths. `qasight init` first shows up at README line 201 | 1, 3 | High | S |
| 2 | **After `qasight init` the user still commits, pushes, opens a pull request and waits for a first run on `main`**, and the pull request comment only has a baseline once `main` has run. A `qasight init --pr` that does those steps with `gh` turns setup into one command | 3, 6 | High | M |
| 3 | **CI history is only visible inside CI.** The Action keeps history in the Actions cache; to look at it in the UI a user needs a team server. `qasight ui --from-github` (download the cached history and open it) would close the loop without a server | 7 | High | M |
| 4 | **A team server starts from a checkout and hand-made secrets.** The compose file lives in `deploy/server/`, the token is made with `openssl`, HTTPS needs your own reverse proxy, the first account is made with `docker compose exec`, and there's no backup on `main`. `qasight server init` could write all of it | 10 | Med | M |
| 5 | **The same test can look "new" and "removed" at once.** After `qasight init` recorded the pytest stage, `qasight try` on pytest's own JUnit file said `test_a` was new *and* removed: a `qasight run` stage and an ingested file don't line up, so mixing the paths (init, `pytest --qasight`, `qasight ingest`, the Action) splits history | 4, 5 | Med | S–M |

Two small bugs found on the way, worth fixing whatever is picked: the header that `qasight init` writes into `qasight.yaml` links to `docs/CLI%20Commands/Command%20Reference/`, but the docs site slugs page paths (`scripts/mkdocs_hooks.py`), so the page is at `docs/cli-commands/command-reference/` (from `qasight/services/project_setup.py:551`); and the [Team Server](../Processes%20&%20Guides/Team%20Server.md) note still says upload-only cycles aren't on the Cycles page, which #176 fixed.

## Step by step

### 1. Discover (README, `/try`, live demo)

- **By hand:** read the README, find the right path for their runner, decide between `pytest --qasight`, `qasight ingest`, `qasight run` and `qasight init`.
- **Already automated:** the [`/try`](https://arieleven.github.io/qasight/try) page reads a JUnit file in the browser; the live demo; `qasight demo` records eight sample runs and opens the UI.
- **Friction:** four ways in, and the one that does the most for you (`qasight init`) isn't among the first three the README shows. The UI's first-run guide (`frontend/src/features/dashboard/DashboardPage.tsx`) says `qasight config init` and greets with "Welcome to Testo" (the name stays QASight). The `/try` page ends with an ingest snippet, not "set this up for my repository". [Setup Prompts](../Prompts%20&%20Snippets/Setup%20Prompts.md) don't mention `qasight init`.
- **Proposals:**
  - **D1 (High, S).** Make `qasight init` the headline everywhere: README hero and Quickstart, Getting Started step 1, the first-run guide, Setup Prompts (the prompt becomes "run `qasight init --dry-run`, check it, then `qasight init`").
  - **D2 (Med, S).** On `/try`, after a file is read: a "Get this on every pull request" card with `pipx run --spec qasight qasight init` (or the `pip install` pair) and the workflow `qasight init` would write.

### 2. Install (pip, Docker, Action, `qasight ui`)

- **By hand:** a Python 3.11+ environment; pick extras (`[api]` for the UI, `[bdd]` for Behave, `[db]`, `[otel]`, `[mcp]` in #249).
- **Already automated:** the Action installs `qasight` itself; the runner image has it; `qasight ui` says which extra is missing and exits `2`; the wheel carries the built UI.
- **Friction:** JavaScript, Go and .NET teams may not have a Python they want to touch. `qasight ui` without `[api]` stops instead of offering to install. A stale environment (an editable install from before `alembic` became a dependency) fails with a raw `ModuleNotFoundError` traceback on the first recorded run.
- **Proposals:**
  - **I1 (Med, S).** Document a no-venv path: `uvx --from 'qasight[api]' qasight ui` / `pipx run --spec qasight qasight`, and use it in the non-Python Setup Snippets (the GitLab, Jenkins and CircleCI templates already install with uv).
  - **I2 (Low, S).** A top-level handler for `ModuleNotFoundError` of a declared dependency or extra: one line saying which `pip install` fixes it, no traceback.
  - **I3 (Low, S).** `qasight ui` offers to `pip install 'qasight[api]'` into the current environment when run in a terminal.

### 3. Set up (`qasight init`)

- **By hand:** run `qasight init`, read the output, commit the three files, push, open a pull request; fix the install line for projects that install test dependencies some other way; add Cypress by hand (detected, not added); in a monorepo, run it per folder (`--scan` is in progress).
- **Already automated:** stack detection for ten runners, `qasight.yaml` with the schema line, `.github/workflows/qasight.yml` (or `--ci gitlab|jenkins|circleci`), `.gitignore` lines, a first recorded run, `--dry-run`, never overwriting an existing pipeline.
- **Friction:** setup ends at "Next: git add ... && git commit", so the real goal (a comment on a pull request) is still four manual steps away. The docs link in the generated YAML header is broken (above).
- **Proposals:**
  - **S1 (High, M).** `qasight init --pr`: create a branch, commit the files, push, open a pull request with `gh`, and trigger the workflow on the default branch (`gh workflow run`) so the first pull request has a baseline. Falls back to printing the commands without `gh`.
  - **S2 (Med, S).** Fix the header link; add a test that every URL `project_setup.py` and `ci_templates.py` write matches a page the docs build produces.
  - **S3 (Low, S).** Add Dependabot entries for `pip` and `github-actions` to what `init` writes when `.github/dependabot.yml` is missing (helps step 11).

### 4. Verify (`qasight try`)

- **By hand:** run the tests so they leave result files, then `qasight try` (or `--from-github`, which needs `gh` signed in).
- **Already automated:** a real ingest and markdown render in a throwaway history, with the local SQLite history copied in so the comparison is real.
- **Friction:** in the fresh-folder run, after `qasight init` recorded the `pytest` stage, `pytest --junitxml` then `qasight try` showed `test_a` as both "new" and "removed". The stored key for the run was `tests.test_a#test_a` in stage `pytest`; the preview's run was a `junit` stage, so the comparison likely keys tests per stage. The preview then shows a confusing first comment exactly when a user is deciding whether to adopt. `qasight try` with no result files says to run the tests; it could offer to.
- **Proposals:**
  - **V1 (Med, S–M).** Compare tests across stage names when one side is a generic `junit` stage (or record ingested pytest files with stage `pytest`), so `qasight run`, `pytest --qasight` and `qasight ingest` of the same suite line up. Fixes step 5 too.
  - **V2 (Low, S).** `qasight try --run`: run the cycle (or the detected test command with its result-file flag) first, then preview.

### 5. First run or import

- **By hand:** choose `pytest --qasight`, `qasight ingest`, or `qasight run`; pick a cycle name and keep it the same everywhere.
- **Already automated:** `qasight ingest` with no arguments finds result files by content and names the cycle after the folder; `pytest --qasight` records branch and commit; `qasight init` records a first run and treats a failing test as recorded.
- **Friction:** the paths name stages differently (see V1), and a user who starts with `pytest --qasight` locally and `qasight init`'s cycle in CI ends up with two histories under one name. Nothing tells them.
- **Proposals:** V1 above; **F1 (Low, S)** a one-line warning in `qasight summary` and the run page when the previous run of the cycle has a different set of stages.

### 6. Wire CI (Action, snippets, secrets, baseline branch)

- **By hand:** commit the workflow; add secrets (`SLACK_WEBHOOK_URL`, `TESTO_API_TOKEN`, a server URL); grant `pull-requests: write` (and `issues: write` for `flaky-issues`) in hand-written workflows; for a team server, write a separate `qasight ingest --server` step.
- **Already automated:** zero-config mode (finds results, `comment-on-pr: auto`, history in the Actions cache); the baseline branch comes from `GITHUB_BASE_REF`; generated GitLab, Jenkins and CircleCI files with history caching; inputs are passed as environment variables.
- **Friction:** the Action has no `server-url` / `server-token` inputs. `qasight ingest` reads `TESTO_SERVER_URL` from the environment so zero-config mode can upload, but that isn't one of the Action's documented inputs, and a cycle run by the Action (`qasight run --ci`) never reaches a server. Pull requests from forks get a read-only token, so the comment is skipped with a warning. The first pull request after setup compares with nothing until `main` has run once (S1 covers it).
- **Proposals:**
  - **C1 (Med, S).** `server-url` and `server-token` inputs on the Action, for both modes (upload the recorded run after `qasight run`).
  - **C2 (Med, S–M).** `qasight init --server URL` writes those inputs and, with `gh`, sets the token secret (`gh secret set TESTO_API_TOKEN`), so secrets aren't a separate trip to the settings page.
  - **C3 (Low, M).** A fork-safe template: the test workflow uploads results as an artifact and a `workflow_run` workflow posts the comment, written by `qasight init --ci github --forks`.

### 7. Read results

- **By hand:** open the job summary or the comment; locally, `qasight summary`, `qasight flaky`, `qasight ui`.
- **Already automated:** job summary, one updated comment per cycle, inline annotations, failing tests first in the UI, per-test pages, Compare, Project health.
- **Friction:** a CI-only user (most zero-config users) has history in the Actions cache and no way to browse it. The cache is also evicted by GitHub after a week without use, and a run that starts with an empty history looks like a first run without saying so.
- **Proposals:**
  - **R1 (High, M).** `qasight ui --from-github [--repo owner/name]`: download the latest cached history (as an artifact the Action also uploads) and open the UI on it. Reuses `qasight try --from-github`'s `gh` plumbing.
  - **R2 (Med, S).** When the Action's history restore misses, say so in the job summary ("no earlier history found: first run, or the cache expired") instead of a silent first run.
  - **R3 (Low, M).** Optional static report on GitHub Pages from the Action, the way this repository's demo is built.

### 8. Flaky handling (quarantine, issues)

- **By hand on `main`:** read `qasight flaky`, paste a `quarantine:` rule into `qasight.yaml` (which `pytest --qasight` and ingest-only users don't have); open issues by hand, or turn on the Action's `flaky-issues` input.
- **Already automated on `main`:** flip-based detection with retries in one run counted as flaky (Playwright, Jest, Xcode, Surefire, Gradle retries); `quarantine:` rules keep a red test from failing `qasight run`; the Action's `flaky-issues` opens one issue per newly flaky test.
- **In #249 (open):** stricter detection (flaky vs broken, same commit, same branch), quarantine from the UI and `qasight quarantine` with no config file, `TESTO_ISSUES=github` opening and closing issues with CODEOWNERS owners.
- **Friction that stays after #249:** in Actions-cache mode the quarantine list lives in each workflow's cache, so a `qasight quarantine add` on a laptop doesn't reach CI without a server or the YAML; and nothing quarantines or releases a test on its own.
- **Proposals:**
  - **Q1 (High, S).** Merge #249 first; it closes most of this step.
  - **Q2 (Med, M).** An opt-in policy, `quarantine: {auto: {flip_rate: 0.3, release_after_green_runs: 10}}`, applied after each recorded run, with the change listed in the pull request comment.
  - **Q3 (Low, S).** `qasight quarantine add` prints the YAML line too when no server or persistent database is configured, so CI-cache users know the laptop list won't reach CI.

### 9. Pull request review comment

- **By hand:** set `comment-on-pr: "true"` and `pull-requests: write` when a cycle is configured (only zero-config mode turns it on by itself).
- **Already automated:** one comment per cycle, edited on later pushes; baseline is the target branch; "New" vs baseline; flaky and slower tests; rerun commands; CODEOWNERS owners and a prefilled issue link.
- **Friction:** the AI failure summary exists only in the UI. Forks (C3).
- **Proposals:** **P1 (Med, M)** an opt-in `ai-summary` Action input that adds the summary to the comment, using a provider key from a secret (the free OpenAI-compatible Gemini route keeps it free). **P2 (Low, S)** `qasight init` already turns comments on; make the README's hand-written workflow example do the same.

### 10. Team use (server image, accounts, runner service, TLS, backups)

- **By hand:** clone the repository for `deploy/server/docker-compose.yml`; make a token with `openssl rand -hex 32`; `docker compose up -d`; put HTTPS in front with your own proxy and set `FORWARDED_ALLOW_IPS`; `docker compose exec server qasight users add ...` for each person; a second compose file for the runner service; backups of the Postgres volume by hand (`qasight backup` arrives with #248); add `--server` and the token to every CI job.
- **Already automated:** the server image with the UI built in, schema migrations at startup, history retention, a protected-reads default, login throttling, a queued-run table, the runner service, a CI smoke test of the whole compose file.
- **Friction:** every item in "by hand" is a separate page section; a small team without an ops person stops at HTTPS.
- **Proposals:**
  - **T1 (Med, M).** `qasight server init [--domain testo.example.com]`: writes `docker-compose.yml` and `.env` with a generated token, an optional Caddy service that gets a certificate on its own, the first admin from `TESTO_ADMIN_USER` / `TESTO_ADMIN_PASSWORD` on first start, and prints the CI lines to paste (or sets them with `gh`, C2).
  - **T2 (Med, S, after #248).** A backup service in the compose file running `qasight backup` nightly with a kept count.
  - **T3 (Low, S).** Fix the stale "Limits today" bullet in [Team Server](../Processes%20&%20Guides/Team%20Server.md).

### 11. Upgrade and maintain

- **By hand:** notice a new release, `pip install -U qasight`, pull the new image, read the CHANGELOG.
- **Already automated:** `@v1` in workflows moves on each stable release; image tag `1`; SQLite and Postgres schemas migrate on first use; history retention prunes old runs.
- **Friction:** nothing tells a laptop or server user that a release exists; a broken environment after an upgrade shows a traceback (I2).
- **Proposals:** **U1 (Med, S)** `qasight doctor` and the UI's footer show "qasight X is out" from PyPI's JSON API, cached a day, off with `TESTO_NO_UPDATE_CHECK=1` and never in CI. **U2 (Low, S)** Dependabot lines from `qasight init` (S3). **U3 (Low, S)** an "Upgrading" section in the CHANGELOG for releases that need an action.

### 12. Go further (plugins, MCP, AI)

- **By hand:** plugins: copy `examples/plugin`, write the entry points, `qasight plugins` to check. MCP (#249): `pip install 'qasight[mcp]'`, then `claude mcp add qasight -- qasight mcp --config ...` or edit `.mcp.json`. AI: open Settings in the UI and paste a key, or switch on the Claude Code provider that uses the local `claude` login.
- **Already automated:** plugin discovery and listing; MCP read tools over the local history; three AI providers with the claude-code one needing no key.
- **Friction:** no CLI for AI settings, so a CI or server setup has to go through the browser; MCP and plugins are copy-from-docs.
- **Proposals:** **G1 (Med, S)** `qasight mcp install [--client claude-code|claude-desktop|cursor]` writes the right config file with this project's path. **G2 (Low, S)** `qasight ai setup` finds `claude` on PATH or an `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/Gemini key in the environment and writes the settings the UI writes. **G3 (Low, S)** `qasight plugins new <name>` writes a package from the example with both entry points and a test.

## All proposals, ranked

Ordered by impact, then by effort.

| Rank | Id | Proposal | Impact | Effort |
|:-:|:-:|---|:-:|:-:|
| 1 | D1 | `qasight init` as the headline path in the README, Getting Started, the UI's first-run guide and Setup Prompts | High | S |
| 2 | Q1 | Merge #249 (quarantine without config, flaky issues, MCP) | High | S |
| 3 | S1 | `qasight init --pr`: branch, commit, push, pull request, first run on `main` | High | M |
| 4 | R1 | `qasight ui --from-github`: browse the CI history without a server | High | M |
| 5 | S2 | Fix the broken docs link `qasight init` writes, and test every generated URL | Med | S |
| 6 | C1 | `server-url` / `server-token` Action inputs for both modes | Med | S |
| 7 | U1 | Update notice in `qasight doctor` and the UI | Med | S |
| 8 | R2 | Say when the Action starts with no history | Med | S |
| 9 | G1 | `qasight mcp install` | Med | S |
| 10 | I1 | `uvx` / `pipx` path for non-Python teams | Med | S |
| 11 | D2 | "Get this on every pull request" card on `/try` | Med | S |
| 12 | V1 | One test identity across `qasight run`, `pytest --qasight` and `qasight ingest` stages | Med | S–M |
| 13 | C2 | `qasight init --server` sets the CI secret with `gh` | Med | S–M |
| 14 | T2 | Nightly backup service in the compose file (after #248) | Med | S |
| 15 | T1 | `qasight server init` with automatic HTTPS and a first admin | Med | M |
| 16 | Q2 | Automatic quarantine and release policy | Med | M |
| 17 | P1 | AI summary in the pull request comment | Med | M |
| 18 | I2, I3, S3, V2, F1, Q3, P2, T3, U3, G2, G3 | Small polish items from the steps above | Low | S each |
| 19 | C3, R3 | Fork-safe comment template; static report on Pages | Low | M |

## Status

Taltal asked for every proposal on 2026-11-01. Built so far:

- #275: D1, D2, I1, S2, T3, P2 (the README already had it; Getting Started now does too), U3.
- #276: V1, V2, F1. Flaky history and test pages still key on the stored key, so one recording method per cycle is still the advice.
- #277: S1 (`qasight init --pr`; the first pull request still has no baseline until it merges, since the workflow isn't on the default branch before that), S3 (`--dependabot`, GitHub Actions updates), C1 (`server-url` / `server-token` inputs), C2 (`qasight init --server` and the secret), R2 (empty-history note in the job summary), G2 (`qasight ai`), P1 (AI summary in the comment), C3 (fork-safe comments, `qasight init --forks`).
- #278: Q2 (`TESTO_QUARANTINE_AUTO`), Q3 (CI hint on `quarantine add`), G1 (`qasight mcp install`), T2 (backup service). Q1 was #249.
- #281: R1 (`qasight ui --from-github`, the Action's `history-artifact`), U1 (update notice in `qasight doctor` and the UI), I2 (one-line hint for a missing extra), I3 (`qasight ui` offers to install `[api]`), T1 (`qasight server init`, first admin from `.env`), G3 (`qasight plugins new`).
- R3 (`qasight export-site`, the Action's `static-site` input): Taltal chose to build it after R1 landed. #283.

Design: [New User Journey Automation - 2026-11-01](../Specs%20&%20ADRs/New%20User%20Journey%20Automation%20-%202026-11-01.md).
