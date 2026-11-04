---
type: spec
status: current
created: 2026-10-07
updated: 2026-11-04
---

# Interview Readiness Fixes - 2026-10-07

## Context

A review of the repo and the live Pages demo, done as a reader would see them in an interview, found a list of bugs and rough edges: the resolver dropped `junit_xml` and `tier`, the dashboard showed "Degraded" because of a MinIO health check, leftover "UQO" names, Compare was hard to read, the nav broke on phones, and the nightly job was always red. All of it was fixed as one batch of parallel pull requests, each kept independent.

## Decision

| Area | Change | Pull request |
|------|--------|--------------|
| Engine | Keep `junit_xml` and `tier` when resolving cycle stages | [#73](https://github.com/arieleven/qasight/pull/73) |
| Engine | Pyramid buckets a run by the tiers it ran with | [#71](https://github.com/arieleven/qasight/pull/71) |
| Engine | Stage workers are honest per framework | [#77](https://github.com/arieleven/qasight/pull/77) |
| Pytest | Keep the target repo's own rootdir | [#69](https://github.com/arieleven/qasight/pull/69) |
| Storage | Drop MinIO and pre-v1.1 run-history compatibility | [#75](https://github.com/arieleven/qasight/pull/75) |
| Storage | Each run gets its own copy of its artifacts | [#68](https://github.com/arieleven/qasight/pull/68) |
| API | Optional token, strict CORS and origin check | [#76](https://github.com/arieleven/qasight/pull/76) |
| API | Bounded execution registry; async SSE tail | [#81](https://github.com/arieleven/qasight/pull/81) |
| CLI | `uqo` alias and legacy frontend redirects removed | [#72](https://github.com/arieleven/qasight/pull/72) |
| Naming | UQO → QASight rename finished in code and UI (env vars `TESTO_*`) | [#78](https://github.com/arieleven/qasight/pull/78) |
| Frontend | Same-cycle compare; honest health for crashed stages | [#82](https://github.com/arieleven/qasight/pull/82) |
| Frontend | Runs named by cycle and time; Compare as tables | [#85](https://github.com/arieleven/qasight/pull/85) |
| Frontend | Top nav collapses into a menu on phones | [#83](https://github.com/arieleven/qasight/pull/83) |
| Demo | Self-hosting CI publishes the UI to GitHub and GitLab Pages | [#67](https://github.com/arieleven/qasight/pull/67) |
| Demo | The read-only Pages demo explains itself | [#84](https://github.com/arieleven/qasight/pull/84) |
| CI | Coverage enforced on the fast suite; nightly fixed | [#79](https://github.com/arieleven/qasight/pull/79) |
| Repo | Dead code and failure injection deleted; root tidied | [#80](https://github.com/arieleven/qasight/pull/80) |
| Docs | Vault readable on GitHub; work log archived | [#74](https://github.com/arieleven/qasight/pull/74) |
| Docs | README top section with demo link and architecture diagram | [#70](https://github.com/arieleven/qasight/pull/70) |

### Second wave

| Area | Change | Pull request |
|------|--------|--------------|
| CI | mypy gates `qasight_api` as well as `qasight` | [#87](https://github.com/arieleven/qasight/pull/87) |
| Code | Root modules moved into subpackages: `db*` → `repository/`, metrics, integrations and report generator → `reporting/`, `triggers` → `config/`; only `paths.py` stays at the root | [#88](https://github.com/arieleven/qasight/pull/88) |
| Docs | Current UI screenshots in the README (`docs/assets/screenshots/`) | [#89](https://github.com/arieleven/qasight/pull/89) |
| Code | Silent `except` blocks in I/O and reporting paths now log or re-raise | [#90](https://github.com/arieleven/qasight/pull/90) |
| Release | Version 1.1.0: `pyproject.toml` bump and `CHANGELOG.md` cut | [#91](https://github.com/arieleven/qasight/pull/91) |

The vault and wiki work done the same day is its own decision: [Docs Vault and Wiki Sync - 2026-10-07](Docs%20Vault%20and%20Wiki%20Sync%20-%202026-10-07.md).

Defaults chosen: the next version is v1.1.0, with a moving `v1` tag for the GitHub Action; no backward-compatibility shims, since there are no external users.

## Consequences

- Breaking for anyone on 1.0: the `uqo` command, `UQO_*` env vars, MinIO storage and pre-v1.1 history are gone. See [CHANGELOG.md](../../CHANGELOG.md).
- v1.1.0 is cut but not tagged or published, and stale branches remain; both need someone with push rights to tags and branch deletion ([Publishing to PyPI § Current state](../Processes%20&%20Guides/Publishing%20to%20PyPI.md#current-state)).
