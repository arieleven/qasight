---
type: guide
status: current
created: 2026-11-12
updated: 2026-11-12
---

# Stability and Deprecation Policy

[Index](../Index.md) · [Product Roadmap](../Roadmap%20&%20Strategy/Product%20Roadmap.md) · [Adoption Blockers - 2026-11-12](../Specs%20&%20ADRs/Adoption%20Blockers%20-%202026-11-12.md)

What you can build on, and how much warning you get before it changes. QASight follows [Semantic Versioning](https://semver.org) from 1.0.0 on.

## What is stable

These don't break within a major version (1.x):

| Surface | Where it's described |
|---------|----------------------|
| CLI commands, their flags and exit codes (`0` to `4`) | [Command Reference](../CLI%20Commands/Command%20Reference.md), [Troubleshooting and Error Codes](../CLI%20Commands/Troubleshooting%20and%20Error%20Codes.md) |
| `qasight.yaml` keys and their meaning; the JSON Schema in `schema/` | [Command Reference](../CLI%20Commands/Command%20Reference.md) |
| HTTP API under `/api/v1/` (routes, request and response fields) | the server's `/docs` page (OpenAPI) |
| `qasight ingest` formats and `--server` uploads | [Team Server](Team%20Server.md) |
| `TESTO_*` environment variables and `DATABASE_URL` | [Team Server](Team%20Server.md) |
| GitHub Action inputs (`arieleven/qasight@v1`) and the GitLab template | [CI-CD Pipeline Setup](CI-CD%20Pipeline%20Setup.md) |
| Docker image tags `1`, `1.x`, `1.x.y` | [Publishing Docker Images](Publishing%20Docker%20Images.md) |
| The run-history database: an upgrade migrates it, nothing is lost | [Team Server § Backups](Team%20Server.md#backups) |

Adding is not breaking: new commands, flags, keys, routes, response fields and enum values can come in any minor release, so read API responses tolerantly.

Not covered: Python imports other than the plugin API (`qasight.plugins`), the web UI's layout and URLs, log and console wording, `--ci` NDJSON event fields not listed in the Command Reference, and anything marked *experimental* in its docs.

## How things are removed

1. **Deprecated in a minor release.** The old spelling keeps working, prints a warning that names the replacement (CLI and config), and the API marks the route `deprecated` in OpenAPI. The CHANGELOG lists it under *Deprecated*.
2. **Kept for at least six months and two minor releases**, whichever is longer.
3. **Removed only in the next major release** (2.0), listed under *Removed* with the migration step.

The 2026-11 rename shows the pattern: `testo`, `testosterone.yaml`, `testo_core` imports and `TESTO_*` variables all still work in 1.x ([Rename to QASight](../Specs%20&%20ADRs/Rename%20to%20QASight%20-%202026-11-04.md)).

## Releases and support

- **Minor releases** (1.x) bring features; **patch releases** (1.x.y) only fixes. Every release has a CHANGELOG entry and runs the [release gate](../Specs%20&%20ADRs/Release%20Scorecard%20-%202026-11-12.md).
- **Security fixes** go into the latest 1.x release ([SECURITY.md](https://github.com/arieleven/qasight/blob/main/SECURITY.md)).
- **Upgrading** is `pip install -U qasight` or a new image tag; the database migrates on start. Take a `qasight backup` first on a team server.
- **Pin what you depend on.** `qasight==1.6.*` in CI, or image tag `1.6`, gets fixes without new features.
