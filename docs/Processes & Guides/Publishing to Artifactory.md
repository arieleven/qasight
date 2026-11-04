---
type: guide
status: current
created: 2026-09-05
updated: 2026-11-04
---

# Publishing to JFrog Artifactory

[Index](../Index.md) · [Publishing to PyPI](Publishing%20to%20PyPI.md) · [Publishing Docker Images](Publishing%20Docker%20Images.md) · [v1.0.0 Publish Execution Plan - 2026-09-05](../Archive/v1.0.0%20Publish%20Execution%20Plan%20-%202026-09-05.md)

Artifactory is a **mirror** of the public release, not a replacement for it. The canonical channels stay PyPI (`qasight`) and GHCR (`qasight-runner`); Artifactory exists so consumers behind a corporate proxy can pull from an internal registry.

Workflow: `.github/workflows/artifactory-publish.yml`.

## Design: inert until configured

Every job is gated on `if: vars.ARTIFACTORY_URL != ''`. Until that repository variable exists the workflow **no-ops** rather than failing. This is deliberate — the workflow can land before the Artifactory repos exist without putting the v1.0.0 release at risk.

## One-time setup (Artifactory admin required)

1. Create a **PyPI-type local repository**, e.g. `testo-pypi-local`.
2. Create a **Docker local repository**, e.g. `testo-docker-local`. Note whether your instance uses subdomain or repository-path Docker access — that determines `ARTIFACTORY_DOCKER_HOST`.
3. Create an **identity token** for a service user with deploy rights on both repos. Use a token, never an account password.

## One-time setup (GitHub repo settings)

Repository **variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Example |
|----------|---------|
| `ARTIFACTORY_URL` | `https://acme.jfrog.io` |
| `ARTIFACTORY_PYPI_REPO` | `testo-pypi-local` |
| `ARTIFACTORY_DOCKER_REPO` | `testo-docker-local` |
| `ARTIFACTORY_DOCKER_HOST` | `acme.jfrog.io` |

Repository **secrets**:

| Secret | Value |
|--------|-------|
| `ARTIFACTORY_USERNAME` | service user |
| `ARTIFACTORY_TOKEN` | identity token |

Setting `ARTIFACTORY_URL` is what switches the workflow on.

## Triggering

- **Automatic:** fires on `release: published`, alongside `publish.yml` and `docker-publish.yml`.
- **Manual:** `workflow_dispatch` with a `version` input (e.g. `1.0.0`) — use this to publish to Artifactory *after* a release that predates the configuration, which is exactly the v1.0.0 case if Artifactory is provisioned late.

Both paths strip a leading `v`: the git tag is `v1.0.0`, the package version is `1.0.0`.

## Consuming

```bash
pip install --index-url https://<user>:<token>@acme.jfrog.io/artifactory/api/pypi/testo-pypi-local/simple qasight==1.0.0
docker pull acme.jfrog.io/testo-docker-local/qasight-runner:1.0.0
```

Prefer a `~/.netrc` entry or `pip config` over inlining the token in the index URL.

## Alternative: JFrog OIDC

`jfrog/setup-jfrog-cli` supports OIDC, removing the stored token — the same posture `publish.yml` already uses for PyPI. It needs an OIDC integration configured on the Artifactory side, so the token path is the documented default. Switch once the integration exists; the workflow's variable contract does not change.

## Known gaps

- No cleanup policy on the local repos yet — every release accumulates.
- Image is not re-scanned before the Artifactory push; it is the same digest `docker-publish.yml` already scanned with Trivy.
