---
type: guide
status: current
created: 2026-11-12
updated: 2026-11-20
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

## Deprecated now

| What | Since | Use instead | Removed in |
|------|-------|-------------|------------|
| Pushing run KPIs to InfluxDB (`INFLUXDB_URL`, `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET`, the `[metrics]` extra) or a Prometheus Pushgateway (`PROMETHEUS_PUSHGATEWAY_URL`, `PROMETHEUS_JOB_NAME`) after each run | 1.8 | OpenTelemetry: an `otel` reporter or [`qasight otel`](../CLI%20Commands/Command%20Reference.md#qasight-otel); a collector forwards to Prometheus, Grafana, InfluxDB or Datadog | 2.0 |

Until then the push still works, best-effort, and logs one warning per process. Why: [Focus Review](../Specs%20&%20ADRs/Focus%20Review%20-%202026-11-20.md).

Renamed commands are not deprecated: `qasight try`, `qasight pool` and `qasight config-db` are aliases of `qasight ci preview`, `qasight lease pools` and `qasight config db`, and stay in 1.x and 2.0 ([Command Reference](../CLI%20Commands/Command%20Reference.md)).

## Migrating from v1.0

v1.1 replaced the Streamlit UI with the React frontend and dropped the Docker-based headless runner (stages run as host subprocesses).

| v1.0 | v1.1 and later |
|------|------|
| Streamlit UI (`testo-ui`, `streamlit run app.py`) | React frontend (`frontend/`), `qasight ui` |
| `uqo` command, `UQO_*` env vars | `qasight`, `TESTO_*` |
| `uqo run --config runs.yaml` (`runs:` list of `test_type`/`cli_args`) | a cycle in `qasight.yaml`, run with `qasight run --cycle <name>` |
| `--ghost`, `--json`, `--stream-json` | `--ci` (NDJSON events, `plan_finished` last) |
| `POST /api/v1/executions` + `/executions/{id}/events` | `POST /api/v1/adhoc-executions` (one framework) or `POST /api/v1/cycles/{cycle}/executions`; status and SSE under `/cycle-executions/{id}` |
| "Legacy Execution" page | Quick Run (`/quick-run`) |
| Tests run in one-off Docker containers (`UQO_RUNNER_IMAGE`, `UQO_RUNNER_PREBUILT`) | stages run as host subprocesses; use `Dockerfile.qasight-runner` as the CI job image if you want isolation |
| Allure results uploaded to MinIO per run | per-run reports under `static/history/<run_id>/` (served at `/history`) and the report archive DB |
| `locust` test type | `equipment: command` with `args: [locust, --headless, …]` |
| Pluggy `plugins/*.py` runner hooks | a framework adapter or reporter in your own package, registered as a `qasight.frameworks` / `qasight.reporters` entry point (see `examples/plugin/`) |

## Releases and support

- **Minor releases** (1.x) bring features; **patch releases** (1.x.y) only fixes. Every release has a CHANGELOG entry and runs the [release gate](../Specs%20&%20ADRs/Release%20Scorecard%20-%202026-11-12.md).
- **Security fixes** go into the latest 1.x release ([SECURITY.md](https://github.com/arieleven/qasight/blob/main/SECURITY.md)).
- **Upgrading** is `pip install -U qasight` or a new image tag; the database migrates on start. Take a `qasight backup` first on a team server.
- **Pin what you depend on.** `qasight==1.6.*` in CI, or image tag `1.6`, gets fixes without new features.
