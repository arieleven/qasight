---
type: spec
status: current
created: 2026-10-31
updated: 2026-11-04
---

# Security and Install Review - 2026-10-31

[Specs & ADRs](README.md) · [Team Server](../Processes%20&%20Guides/Team%20Server.md) · [Project review](../project-review.md) · [SECURITY.md](https://github.com/arieleven/qasight/blob/main/SECURITY.md)

## Context

A review of the whole app for security problems (auth modes, cookies, report serving, the container runner service, resource pools and provider commands, secrets in failure evidence, dependencies, Docker images, CI workflows), plus fresh installs of the released `qasight` 1.4.0 the way a new user does them. The earlier [project review](../project-review.md) passes (S1 to S12) are not repeated; risks they accepted (report pages run script on the API's origin unless `TESTO_REPORTS_ORIGIN` is set, the API runs test commands by design, token-mode login has no throttle) stay accepted.

Every finding below was reproduced with a script or a test unless it says otherwise.

## Findings

Ranked by severity. "Fixed" means fixed in the pull request that added this note, with a test that fails without the fix.

| ID | Severity | Area | Finding | Status |
|----|----------|------|---------|--------|
| R1 | High | Container runner | With `TESTO_RUNNER=container`, `${env:...}` in a stage's `args` or `env:` was filled from the **server's** environment and sent to the throwaway container, so a runner could copy `TESTO_API_TOKEN` (the admin credential that also signs every session cookie), `TESTO_RUNNER_TOKEN`, `DATABASE_URL` and AI keys into a stage: runner to admin | Fixed: in container mode `${env:...}` can't read `TESTO_*`, `DATABASE_URL`, `POSTGRES_*` or `*_API_KEY` / `*_TOKEN` / `*_SECRET` / `*_PASSWORD` / `*_DB_URL` |
| R2 | High | Container runner, pools | A `per: stage` resource pool's `on_take` / `on_check` / `on_release` hooks run with `shell=True` in the API server's process, even with `TESTO_RUNNER=container`. A runner (or a repo's `qasight.yaml`) gets a shell on the server with its environment | Fixed (P1, approved 2026-11-01): with `TESTO_RUNNER=container`, a `per: stage` pool with hooks is refused before anything is leased; `per: test` hooks run inside the container |
| R3 | High | Reports | An `allure-results` upload (`qasight ingest`, `POST /api/v1/ingest`) kept each attachment's own file name, so an attachment named `page.html` was served from `/history` as a normal page on the API's origin: stored script that acts with the viewer's session. In accounts mode a runner could use it against an admin | Fixed: copies are stored as `<uuid>-attachment<ext>`, and every file under a raw `allure-results/` folder is served in a CSP sandbox |
| R4 | Medium | Local API | DNS rebinding: a web page that re-points its own hostname at `127.0.0.1` could read every GET route of a token-less API (the local default and `qasight ui`): runs, logs, reports. Writes were already refused by the Origin check | Fixed: without a token the API answers only loopback `Host` names, plus `TESTO_ALLOWED_HOSTS` |
| R5 | Medium | Local API | `TESTO_CORS_ORIGINS=*` skipped the Origin check for writes even without a token, so any web page could start runs (run commands) | Fixed: `*` waives the check only when a token is required anyway |
| R6 | Medium | GitHub Action | `action.yml` pasted `${{ inputs.* }}` straight into its shell scripts. A workflow passing a branch name or PR title as `cycle` or `config-path` got script injection | Fixed: inputs go through `env:`; a contract test refuses `${{` in any `run:` |
| R7 | Medium | Container runner, pools | Stages with `resources:` get `TESTO_LEASE_DB_URL` set to the server's database URL (with its password), and `TESTO_API_TOKEN` too when `TESTO_SERVER_URL` is set on the server | Proposal P2 |
| R8 | Medium | Pools | A take request carries the pool spec, and the server trusts it: a runner can drop `max_per_user`, set a `lease_ttl` of 10^15 seconds, or overwrite another team's item `data` / `labels`, which every reader then sees | Proposal P3 |
| R9 | Medium | Container runner | Stage containers mount the shared artifacts volume read-write, and the server's `ai_settings.json` lives there. A stage can point the AI provider at its own host with `api_key_env_var: TESTO_API_TOKEN`; after a restart an AI summary sends the token there. Read from the code, not run | Proposal P4 |
| R10 | Low | Accounts | Removing a user and adding the same name again revived the old person's cookies, because the new row starts at session version 1 again | Fixed: cookies carry the account's creation time |
| R11 | Low | AI provider | Provider requests followed redirects with `Authorization` / `x-api-key`, so a redirect sent the key to another host | Fixed: redirects are refused |
| R12 | Low | Redaction | Failure evidence (stored, and sent to the AI provider) missed GitHub, GitLab, npm, Slack, AWS and Google tokens, JWTs, `Basic` credentials, passwords in URLs, `password=` values, quoted JSON keys and private key blocks | Fixed: all of these are masked; `password`, `DATABASE_URL`, `privateKey` and `cookie` fields too |
| R13 | Low | Dev compose | The root `docker-compose.yml` published Postgres (password `admin`) on every interface | Fixed: `127.0.0.1` only |
| R14 | Low | Token mode | The token-mode session cookie is an HMAC of the token with no expiry inside it: a copied cookie works until the token changes, and signing out only clears one browser | Accepted for now: the guide says to change the token to sign everyone out |
| R18 | Low | CI, GitHub Action | Two third-party actions used moving refs: `pypa/gh-action-pypi-publish@release/v1` (runs with the PyPI publishing identity) and `wagoid/commitlint-github-action@v6`; the Marketplace action's own `actions/setup-python@v5` too, which every user of the action runs. GitHub's and Docker's own actions in the workflows stay on major tags | Fixed: those three are pinned to a commit SHA with the version in a comment; Dependabot keeps them current |
| R15 | Info | Accounts | An account literally named `token` is indistinguishable from the shared token in the audit log | Proposal: reserve the name |
| R16 | Info | Runner service | The docker CLI runs with the stage's variables in its own environment. Harmless for the static `docker` binary; with `TESTO_RUNNER_DOCKER=podman`, `LD_PRELOAD` and `CONTAINERS_CONF` would reach the engine. Unverified | Proposal: build the CLI's environment from a clean base |
| R17 | Info | CI | `artifactory-publish.yml` puts the Artifactory token in a URL on a command line (masked in logs, visible in the runner's process list) | Low priority; only runs on release |

Looked at and found solid: the runner service's request checks (constant-time token, argv and cwd limited to the mounts, fixed image and mounts, no docker flags), the app-wide role guard (a viewer reaches no write; lease ids never appear in reads), static mounts and upload file names (no traversal), password hashing (scrypt, salted, dummy hash against user enumeration, throttled), AI keys (never written to disk, status only says whether one is present), Dockerfiles (all run as a non-root `qasight` user), GitHub workflows (no `pull_request_target`, secrets only on trusted triggers).

### Dependencies

`pip-audit` on a fresh `qasight[api,db,bdd]==1.4.0`: no known vulnerabilities. `npm audit` in `frontend/`: nothing in production dependencies; one moderate `minimatch` advisory in the ESLint toolchain (dev only, fix needs a breaking ESLint major).

## Fresh installs

| Check | Result |
|-------|--------|
| `pip install qasight==1.4.0` in clean venvs on Python 3.11, 3.12, 3.13 | Works in about 15 s, `pip check` clean, `qasight --version` and `--help` work |
| README Quickstart: `pytest --junitxml`, `qasight ingest`, `qasight summary`, `pytest --qasight` | Works. `qasight summary` after the first ingest says it needs two runs, which the README's "run again" hints at |
| `qasight config init` then `qasight run --cycle smoke-test` | Works in an activated venv. With `qasight` from pipx or `uv tool` the stage fails with `pytest: error: unrecognized arguments: --alluredir`, because the `pytest` on PATH has no allure-pytest. Fixed: `qasight run` now warns first and says what to install |
| `pip install 'qasight[api]'`, `qasight ui --no-open` | UI, `/runs` deep link and the API answer; security headers present |
| `docker compose config` for the dev compose and the team server (with the runner overlay) | Valid. Building the images was not tested here: the review's sandbox can't reach PyPI from inside a Docker build. The images are rebuilt and scanned by `docker-publish.yml` on each release |

A plain install also leaves `artifacts/` and `testo_history.db` in the working directory next to `.testo/`; worth one line in the Quickstart (Info).

## Proposals

Bigger items, not started; each needs a yes first.

- **P1, R2:** done: refused in container mode (see R2). Running those hooks through the runner service instead stays possible later if someone needs `per: stage` hooks there.
- **P2, R7:** container stages lease through the API (`TESTO_SERVER_URL`) with a lease-only credential scoped to the run, never the database URL or the admin token.
- **P3, R8:** pin a pool's spec on the server (first registration, or admin-defined) and ignore limits a client sends; small first step: cap `lease_ttl` server-side.
- **P4, R9:** keep `ai_settings.json` out of the volume stages mount, then give each run its own artifacts subpath.

## Consequences

- Account users sign in again once after upgrading (R10 changed the cookie).
- A token-less API reached under a name other than `localhost`, `127.0.0.1` or `::1` answers `421` until that name is in `TESTO_ALLOWED_HOSTS`; with a token, or `TESTO_ALLOW_UNAUTHENTICATED=1`, nothing changes.
- `TESTO_CORS_ORIGINS=*` without a token no longer lets other origins write.
- Allure attachments uploaded with `qasight ingest` get new file names in the stored run; links in the run and test pages follow them.
