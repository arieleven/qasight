---
type: spec
status: current
created: 2026-11-12
updated: 2026-11-12
related: "External Audit Tools - 2026-11-11"
---

# Release Gate - 2026-11-12

[Index](../Index.md) · Taltal asked on 2026-11-12 to run the third-party checks from [External Audit Tools](External%20Audit%20Tools%20-%202026-11-11.md) in CI before every release, so a version is not published until they pass.

## Context

No single product runs all of these checks on a release candidate. Each tool has a command line or Docker image, and the usual way to combine them is a release gate: a slow workflow that must pass before a release is published. `release-gate.yml` already existed, but it only ran the external provider suite and only by hand.

The checks fall into two groups:

- **Fast checks**, which take a minute or two and run on every pull request.
- **Slow checks**, which take 10 to 40 minutes or need a running server. These run before a release.

## Decision

**On every pull request** (`ci.yml`). The new `security` job runs:

- gitleaks over the whole git history. Fake tokens in the redaction tests are allowed in `.gitleaks.toml`.
- Bandit at medium severity and confidence. Reviewed exceptions carry `# nosec <id>`.
- pip-audit on every extra's dependencies.

The `frontend` job also runs `npm audit --omit=dev --audit-level=high`. zizmor already ran in the `format` job.

**Before a release** (`release-gate.yml`). The gate runs:

- on the pull request that changes `version` in `pyproject.toml`
- on `main` when that bump lands
- by hand

Each run has these jobs:

| Job | Tool | Fails when |
|-----|------|------------|
| `api` | Schemathesis 4.33 | Any request is answered with a 5xx |
| `api` | OWASP ZAP 2.17 (Docker image pinned by digest; plan in `scripts/release_gate/zap-plan.yaml`) | A medium or high finding is not accepted in `scripts/release_gate/zap_check.py` |
| `api` | Locust, 50 users for 60 s, one of them a CI job uploading a 300-test report (`scripts/release_gate/locustfile.py`) | Any request fails, or p95 is over 2.5 s |
| `ui` | Lighthouse CI on the static demo site (`scripts/release_gate/lighthouserc.json`) | Performance is under 60, accessibility under 100, best practices or SEO under 90, or CLS over 0.1 |
| `fuzz` | Hypothesis, 200 generated files per format (`scripts/release_gate/fuzz_importers.py`) | An importer raises instead of reporting the file as unreadable |
| `mutation` | mutmut on the flaky, attention and retention logic (`[tool.mutmut]` in `pyproject.toml`) | The score is under 85% |
| `npm_audit` | npm audit on the whole frontend tree | Any high or critical advisory |
| `release_scorecard` | `qasight scorecard` on this commit and the last release tag ([Release Scorecard](Release%20Scorecard%20-%202026-11-12.md)) | The overall score is under the `min_score` input (default 60), or, with `no_drop`, a tool scored lower than on the last release |

The `api` job tests against the history of one real `sample-pytests` run. Schemathesis and ZAP skip the endpoints that start runs, call an AI provider, delete history or run configured resource commands. Schemathesis also skips the PDF export, which answers 501 in a source checkout without a bundled UI.

A `release gate` job passes only when every job above passed. `publish.yml` starts with a job that looks for a passing `release gate` job on the tag's commit and stops the PyPI upload without one. A by-hand run of `publish.yml` can set `skip_gate` to publish anyway.

The external provider suite moved into the same workflow. It runs only by hand, with the `external` input, because it needs the `TESTO_E2E_*` secrets.

## Consequences

- Measured locally on 2026-11-12, before the thresholds were set:
  - mutation score 87.6% (722 of 828 mutants killed; it was 68% before the 2026-11-11 tests)
  - Lighthouse on mobile: performance 85-91, accessibility 100, best practices 96, SEO 100, CLS 0.02
  - ZAP: no medium or high finding
  - fuzzing: no unexpected exception
  - Schemathesis: no 5xx outside the PDF export
- A release takes up to about 40 minutes longer, and the gate runs twice: on the bump's pull request and again on `main`.
- A release cut from a commit after the bump, or from a branch, needs a by-hand run of `release-gate.yml` on that commit first.
- Schemathesis enforces only "no server errors". Its stricter checks (undocumented status codes, schema-valid requests rejected) report 38 mismatches between the OpenAPI document and the API today. Fixing those is separate work.
- `docker-publish.yml` and `artifactory-publish.yml` are not gated yet. They run on the same Release, so a red gate stops PyPI but not the images.

See [CI-CD Pipeline Setup](../Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md) for every workflow and [Publishing to PyPI](../Processes%20&%20Guides/Publishing%20to%20PyPI.md) for the release steps.
