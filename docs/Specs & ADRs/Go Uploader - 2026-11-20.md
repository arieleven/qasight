---
type: spec
status: current
created: 2026-11-20
updated: 2026-11-20
---

# Go Uploader - 2026-11-20

[Specs & ADRs](README.md) · [Upload Without Python](../Processes%20&%20Guides/Upload%20Without%20Python.md) · [Ingest and Team Server - 2026-10-22](Ingest%20and%20Team%20Server%20-%202026-10-22.md)

## Context

Sending a CI job's results to a team server meant `pip install qasight` first, then `qasight ingest --server`. That is slow on every job, and fails on images with no Python or an old one, and in locked-down networks where installing packages is not allowed. Popular tools ship their CI side as one small binary for this reason (Codecov's uploader, the GitHub CLI, ReportPortal's agents).

## Decision

- A separate program, `qasight-upload`, in `uploader/`, written in Go: one static file per platform (Linux, macOS, Windows; amd64, arm64), Go's standard library only.
- It only uploads, with the existing `POST /api/v1/ingest` request. The server, the engine, the UI and every other feature stay in Python, entered through `CycleRunService` as before. No API change.
- It mirrors the Python client's rules: the same flags and variables (`TESTO_SERVER_URL`, `TESTO_API_TOKEN`, `TESTO_ENV`, `TESTO_RELEASE`, `TESTO_AUTO_METADATA`), the same CI provenance detection, environment and release defaults, exit codes (0, 1, 2, 3) and the same `ingest_uploaded` JSON event, so a run looks the same whichever client sent it.
- Retries only where they can't record a run twice: the request never reached the server, or it answered 429, 502 or 503.
- Optional: it is not in the README headline or the core `--help`. `qasight ingest` stays the documented way where Python is present.
- Binaries, a checksum file and a signed build provenance attestation are attached to every GitHub Release by `.github/workflows/uploader.yml`.

## Consequences

- A second language in the repository. It is small (one Go package, no dependencies), and CI keeps it honest: `scripts/upload_binary_smoke.sh` uploads with the built binary to a real `qasight-api` from the same commit on every pull request that touches the ingest route, its models or the uploader, so an API change that breaks it fails there.
- The ingest request is now a contract with released binaries that people pin: changes to it must stay backward-compatible, under the [Stability and Deprecation Policy](../Processes%20&%20Guides/Stability%20and%20Deprecation%20Policy.md).
- Python-only features of `qasight ingest` (`--coverage` parsing, finding files with no arguments, `--from-github`, local recording) are not in the uploader. Coverage could be added later as two numeric fields the server already accepts.
- The CI provenance rules now live in two places (`qasight/services/ci_provenance.py` and `uploader/metadata.go`); a new CI provider is added to both.
