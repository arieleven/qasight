---
type: spec
status: current
created: 2026-11-15
updated: 2026-11-15
---

# Closed Network Install - 2026-11-15

[Specs & ADRs](README.md) · Guide: [Closed Network Install](../Processes%20&%20Guides/Closed%20Network%20Install.md)

## Context

First users are likely to be teams on networks with no internet: a self-hosted GitLab, an internal Docker registry, maybe a PyPI mirror, and an AI model served inside the network. A read of the 1.7 code found that QASight itself needs nothing from outside once installed (the UI and fonts ship in the wheel, no telemetry, the only outbound call is the PyPI update check, the GitLab comment uses `CI_API_V4_URL`, and `openai_compatible` takes any base URL). What was missing was a way to get it in and to point it at internal sources:

- The compose files hard-coded `ghcr.io/arieleven` and Docker Hub's `postgres`.
- The GitLab template hard-coded `python:3.11-slim` and always ran `pip install`.
- `qasight init` CI jobs always fetched uv from `astral.sh`.
- The server compose file passed no AI or update-check variables, so a model inside the network had to be set by hand on the Settings page.
- No guide said any of this.

## Decision

- `scripts/offline/bundle.sh` (run with internet) packs the wheels for the target's Python and CPU, the images (`docker save`), the GitLab template and `scripts/offline/install.sh` into one `.tar.gz` with `SHA256SUMS`. `install.sh` (run inside) checks the sums, installs the CLI with `pip --no-index`, loads the images and, with `--registry`, pushes them.
- The compose files read `TESTO_REGISTRY` and `TESTO_POSTGRES_IMAGE` (defaults unchanged), and pass `TESTO_NO_UPDATE_CHECK`, `TESTO_AI_PROVIDER`, `TESTO_AI_BASE_URL`, `TESTO_AI_MODEL`, `TESTO_AI_TIMEOUT_S` and `OPENAI_COMPATIBLE_API_KEY` to the server.
- The GitLab template takes `TESTO_IMAGE` and `TESTO_INSTALL` (`auto` installs only when the image has no `qasight`); pip already reads `PIP_INDEX_URL`.
- `qasight init` CI jobs skip uv and the install when `qasight` is already on the image's `PATH`.
- Scripts, not a `qasight` command: packing happens before QASight is installed anywhere, and needs Docker and pip, not QASight.

## Consequences

- One file to carry in, verified on arrival; upgrades are the same steps with a new bundle.
- Defaults are unchanged, so nothing changes for users with internet.
- The image list in the bundle is fixed to what compose uses; a PDF-capable server image is a manual extra build (noted in the guide).
- GitHub-only features (issues, `try --from-github`, backfill, setup PR) stay outside; the guide lists them.
