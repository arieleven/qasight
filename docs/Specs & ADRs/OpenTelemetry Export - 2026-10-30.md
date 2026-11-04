---
type: spec
status: current
created: 2026-10-30
updated: 2026-11-04
---

# OpenTelemetry Export - 2026-10-30

Part of the mainstream-adoption work ([Specs & ADRs](README.md)).

## Context

Teams that already run Datadog, Grafana (Tempo), Honeycomb or Jaeger want test runs next to the services they test, in the tool they already open. All of them take OpenTelemetry traces over OTLP. QASight kept runs only in its own history and UI, and [Compared With Other Tools](../Compared%20With%20Other%20Tools.md) told Datadog teams to look elsewhere.

## Decision

- **One trace per run.** A root span for the run, a child span per stage, and a child span per test under its stage. Tests keep their real start and stop times from the stored results (Allure `start` / `stop`); a stage spans its tests, or is laid out from its recorded `duration_s` when it has none. Failed and broken tests, red stages and red runs get status `ERROR`, with the failure message on tests.
- **Built from run history.** `qasight/services/otel_export.py` reads the stored run (`CompletedRunView`) and its snapshot, so any recorded run can be sent: right after `qasight run` by the `otel` reporter, or later by `qasight otel --cycle/--run-id` (after `qasight ingest` or `pytest --qasight` too). This is the same split as the [Slack and Teams alerts](Alerts%20Where%20Developers%20Look%20-%202026-10-22.md).
- **Attributes follow the OpenTelemetry semantic conventions where they exist** (`test.suite.name`, `test.suite.run.status`, `test.case.name`, `test.case.result.status`, `vcs.ref.head.name`, `vcs.ref.head.revision`, `vcs.ref.base.name`), plus `testo.*` for the rest (run id, cycle, stage, framework, raw status, counts). `service.name` is `qasight` unless `service_name` / `--service-name` or `OTEL_SERVICE_NAME` say otherwise.
- **Standard configuration.** Endpoint, headers and protocol come from the usual `OTEL_EXPORTER_OTLP_*` variables, so a collector already set up for the services works unchanged. The reporter can override them; `qasight otel` takes `--endpoint` and `--protocol` but never headers, because they usually hold API keys (same rule as webhook URLs).
- **Off by default, optional dependency.** Nothing is sent unless the `otel` reporter is configured or `qasight otel` is run. The SDK and the OTLP/HTTP exporter are the `[otel]` extra, so `pip install qasight` stays light. OTLP/gRPC needs `opentelemetry-exporter-otlp-proto-grpc` on top, since `grpcio` is heavy.
- **Errors are reported, not logged.** Spans are collected in memory and exported in batches of 512, and a batch the collector refuses fails the reporter (or exits `3`) instead of disappearing into a log line. The trace is built as plain data first, so `--dry-run` and the tests need no SDK.

## Consequences

- Test runs show up in any OTLP backend as traces, searchable by run id, cycle, branch and commit. Slow tests are visible on a timeline; a failing test is an error span.
- One trace per export: sending the same run twice gives two traces.
- Not done: linking test spans to the traces of the service under test (that needs trace context propagated into the tests), metrics or logs over OTLP, and a "View trace" link in the UI.
- Commands and options: [Command Reference § `qasight otel`](../CLI%20Commands/Command%20Reference.md#qasight-otel).
