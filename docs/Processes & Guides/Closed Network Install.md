---
type: guide
status: current
created: 2026-11-15
updated: 2026-11-20
---

# Closed Network Install

[Index](../Index.md) · [Team Server](Team%20Server.md) · [CI-CD Pipeline Setup](CI-CD%20Pipeline%20Setup.md) · [Closed Network Install - 2026-11-15](../Specs%20&%20ADRs/Closed%20Network%20Install%20-%202026-11-15.md)

Running QASight on a network with no internet (air-gapped), with a self-hosted GitLab and, optionally, an AI model inside the network. You pack everything on a machine that has internet, carry one file in, and install it there.

## What needs the internet

Once installed, nothing does. The web UI is inside the Python package (`qasight_api/ui_dist`) and its fonts are bundled, so pages load nothing from a CDN, and QASight sends no telemetry. The one outbound call is the daily "is a newer qasight out?" check against PyPI, already off in CI; set `TESTO_NO_UPDATE_CHECK=1` everywhere else.

Getting it in is the part that needs internet:

| Piece | Normally from | On a closed network |
|-------|---------------|---------------------|
| The CLI (`pip install qasight`) | PyPI | The bundle's `wheels/`, or your PyPI mirror (Nexus, Artifactory, devpi) |
| Team server images | `ghcr.io/arieleven`, Docker Hub (`postgres`) | The bundle's `images/`, pushed to your registry |
| GitLab CI template | `include: project: arieleven/qasight` and `python:3.11-slim` | The bundle's `gitlab/qasight.gitlab-ci.yml`, committed into your project, with your own image |
| `qasight init` CI jobs | uv from `astral.sh`, then PyPI | An image that already has `qasight` (the jobs then download nothing) |
| AI summaries | OpenAI, Anthropic, Gemini | Any OpenAI-compatible model inside the network |

## 1. Pack it (machine with internet)

From a clone of the repository, checked out at the release tag:

```bash
scripts/offline/bundle.sh                                  # this checkout's version, linux/amd64
scripts/offline/bundle.sh --python 3.11 --extras api,db,bdd --runner
scripts/offline/bundle.sh --no-images                      # the CLI only; no Docker needed
```

It writes `dist-offline/qasight-offline-<version>-linux-<arch>.tar.gz` with:

- `wheels/`: `qasight[<extras>]` and every dependency as wheels for the target's Python versions (`--python`, default `3.11,3.12,3.13`) and CPU (`--arch amd64|arm64`), not this machine's, so a Mac can pack for a Linux server.
- `images/`: `qasight-server`, `qasight-runner` and `postgres:16-alpine` (`docker save`), plus `qasight-runner-service` with `--runner`.
- `gitlab/qasight.gitlab-ci.yml`, `gitlab/qasight-every-job.gitlab-ci.yml` (a monorepo, below), `install.sh`, `bundle.env` (what was packed) and `SHA256SUMS`.

The published server image has no Chromium, so the Report page offers PowerPoint, Excel and HTML but not PDF or PNG. For those, build `Dockerfile.qasight-server` with `--build-arg TESTO_REPORT_PDF=1` on the machine with internet and `docker save` it next to the others.

## 2. Install it (inside)

```bash
tar -xzf qasight-offline-1.7.0-linux-amd64.tar.gz && cd qasight-offline-1.7.0-linux-amd64
./install.sh                                        # checks SHA256SUMS, CLI into ./venv, docker load
./install.sh --registry registry.example.local/qa   # and tags + pushes each image there
```

`--no-venv` installs into the current Python, `--no-cli` / `--no-images` skip a half. pip runs with `--no-index`, so it never tries the network. At the end it prints the `.env` lines for the team server.

## 3. Team server

```bash
venv/bin/qasight server init qasight-server     # compose file and .env with fresh secrets
cd qasight-server
cat >> .env <<'EOF'
TESTO_REGISTRY=registry.example.local/qa
TESTO_SERVER_TAG=1.7.0
TESTO_POSTGRES_IMAGE=registry.example.local/qa/postgres:16-alpine
TESTO_NO_UPDATE_CHECK=1
EOF
docker compose up -d
```

Without `--registry`, the images are already in that machine's Docker: set `TESTO_SERVER_TAG` to the version and leave `TESTO_REGISTRY` out. Everything else (accounts, backups, retention, the variables) is in [Team Server](Team%20Server.md). For sign-in, GitLab itself can be the single sign-on provider: add an application in GitLab (scopes `openid`, `email`, `profile`) with the redirect URL `https://<your server>/api/v1/auth/oidc/callback`, then set `TESTO_AUTH=accounts` and `TESTO_OIDC_ISSUER=https://<your gitlab>` with the id and secret ([Single sign-on](Team%20Server.md#single-sign-on)).

## 4. GitLab CI

Commit `gitlab/qasight.gitlab-ci.yml` into your project and include it locally; `include: project:` works too once the QASight repository is mirrored into your GitLab.

```yaml
include:
  - local: .gitlab/qasight.gitlab-ci.yml

variables:
  TESTO_CYCLE: smoke
  TESTO_IMAGE: registry.example.local/qa/python-qasight:3.11   # has qasight: nothing is downloaded
  # or keep a plain Python image and install from your mirror:
  # TESTO_IMAGE: registry.example.local/python:3.11-slim
  # PIP_INDEX_URL: https://nexus.example.local/repository/pypi/simple
```

The template installs `TESTO_PACKAGE` only when the image has no `qasight` (`TESTO_INSTALL: auto`). An image with it baked in, built inside from the bundle:

```dockerfile
FROM registry.example.local/python:3.11-slim
COPY wheels /tmp/wheels
RUN pip install --no-index --find-links /tmp/wheels "qasight[api,db]" && rm -rf /tmp/wheels
```

Jobs that [`qasight init --ci gitlab`](../CLI%20Commands/Command%20Reference.md#qasight-init) writes run your tests in your language's image and record them after; they skip installing when `qasight` is already on the image's `PATH`, so add the same `pip install --no-index` line to that image. To upload every pipeline to the team server, add `qasight ingest --server https://<your server> --token "$TESTO_API_TOKEN"` ([Upload from CI](Team%20Server.md#upload-from-ci)).

### A monorepo: one include records every job

A large repository already has its own jobs, written by many teams. Rather than a QASight job per suite, include `gitlab/qasight-every-job.gitlab-ci.yml` once at the top of `.gitlab-ci.yml`. It adds a `default:` `after_script` to every job: when the job wrote test results, they are uploaded to the [team server](Team%20Server.md) as a run of a cycle named after the job (`QASIGHT_CYCLE` on a job overrides it); a job without results records nothing, and the job's outcome never changes.

```yaml
include:
  - local: .gitlab/qasight-every-job.gitlab-ci.yml
# CI/CD variables: TESTO_SERVER_URL and TESTO_API_TOKEN (masked)
```

- **Results.** `qasight ingest` finds them by content anywhere in the checkout: pytest needs `--junitxml=<file>` (or `--junitxml` in its `addopts`), Behave `--junit` (writes `reports/TESTS-*.xml`), BehaveX writes `output/behave/*.xml` on its own. Files written before the job started (GitLab's `CI_JOB_STARTED_AT`), such as earlier jobs' artifacts, are left out, so a later job doesn't record them again. A BDD suite's one-file-per-feature output is packed into a few files when it passes the server's 200-file limit.
- **`qasight` in the image.** It needs Python 3.11 or newer: bake it in from the bundle's wheels (the Dockerfile above), or set `QASIGHT_INSTALL: auto` with `PIP_INDEX_URL` pointing at your mirror. For images without Python 3.11, set `QASIGHT_UPLOAD` to the [`qasight-upload`](Upload%20Without%20Python.md) binary (one file, no Python; for example mounted read-only into every job by the runner's `volumes`); it sends the globs in `QASIGHT_RESULTS`.
- **Jobs with their own `after_script`** replace the default; add `- !reference [.qasight-record, after_script]` as their first line.

### A monorepo without touching a job: read GitLab's test reports

Jobs that already declare `artifacts: reports: junit:` (for GitLab's own merge request test widget) need no change at all. A command on the team server reads each pipeline's test report through GitLab's API and records every job as a cycle, dated when it ran; its first pass fills the history from past pipelines:

```bash
# on the team server, every 15 minutes (cron or a systemd timer)
docker compose exec -T -e TESTO_GITLAB_TOKEN=glpat-... server \
  qasight ingest --from-gitlab --gitlab-url https://gitlab.example.local --project group/mono --runs 100
```

The token needs `read_api` on the project. Pipelines already recorded, by an earlier pass or by the include above, are skipped, so the two can run side by side ([`qasight ingest --from-gitlab`](../CLI%20Commands/Command%20Reference.md#qasight-ingest)).

[`qasight init --scan --ci gitlab`](../CLI%20Commands/Command%20Reference.md#qasight-init) is the other way: it writes a job per runner per folder (pytest, Behave, BehaveX and the rest), for a repository that has no CI yet.

The merge request comment goes to your GitLab's own API (`CI_API_V4_URL`, set by GitLab), never gitlab.com; add a `TESTO_GITLAB_TOKEN` CI/CD variable with `api` scope.

## 5. An AI model inside the network

Any server that answers OpenAI's `POST /v1/chat/completions` works: vLLM, Ollama, LiteLLM, TGI, LocalAI. On the team server, in `.env`:

```bash
TESTO_AI_PROVIDER=openai_compatible
TESTO_AI_BASE_URL=http://llm.example.local:8000/v1
TESTO_AI_MODEL=qwen2.5-coder-32b
OPENAI_COMPATIBLE_API_KEY=...        # only if the model asks for one
```

or pick "OpenAI-compatible" on the Settings › AI page and type the URL. The `.env` settings win over the page (the server reads them when it starts, default timeout 60 s, `TESTO_AI_TIMEOUT_S` for a slow model); changes made on the page then last until the next restart. CI jobs take the same `TESTO_AI_*` variables ([`qasight ai`](../CLI%20Commands/Command%20Reference.md#qasight-ai)). Since the model is yours, turning on "share the failing test's source" sends nothing outside.

If internal services use certificates from your own CA, give it to both HTTP stacks: `SSL_CERT_FILE=/path/ca.pem` (AI calls) and `REQUESTS_CA_BUNDLE=/path/ca.pem` (GitLab comments, uploads), or add the CA to the image's trust store.

## What stays outside

These need GitHub or the internet and are simply unused on a closed network; nothing else depends on them:

- `qasight issues` (GitHub issues for flaky tests), `qasight ci preview --from-github`, history backfill from GitHub, and the setup pull request (`gh`).
- The GitHub Action and `npx qasight` (npm).
- Scorecard tools that download their own databases (Trivy, Semgrep rules): bring their offline databases or leave them off ([Release Scorecard](../Specs%20&%20ADRs/Release%20Scorecard%20-%202026-11-12.md)).
- The docs site: the same notes are in `docs/` in the repository.

## Upgrading

Pack the new version, carry it in, run `./install.sh` again and bump `TESTO_SERVER_TAG`. The server upgrades its database schema in place when it starts ([Team Server § Storage](Team%20Server.md#storage)).
