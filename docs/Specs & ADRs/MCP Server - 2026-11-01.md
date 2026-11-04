---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-04
---

# MCP Server - 2026-11-01

Part of the [feature gaps](../Roadmap%20&%20Strategy/Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md) work (G4) ([Specs & ADRs](README.md)).

## Context

Coding agents fix red builds by reading CI logs, which are long and lose what the run history already knows: which failures are new, which tests are flaky, what the run before looked like. Datadog, Trunk, Currents, Allure TestOps and Buildkite ship MCP servers so an agent can ask their test analytics instead. QASight had every answer in its services and API but no way for an agent to ask.

## Decision

- **`qasight mcp`, stdio, local history.** The agent starts the server as a subprocess, the usual way for Claude Code, Claude Desktop and Cursor. It reads the same history as the CLI (`DATABASE_URL`, else the config's `database.url`, else the project's SQLite file). `--config` moves the working directory to the config's folder first, because agents start servers from anywhere.
- **Tools are thin wrappers over existing services.** `qasight/mcp_server.py` (`HistoryTools`) calls `run_failures.load_run_failures` (with its rerun commands), `flaky_service.find_flaky_tests`, `test_history`, `DeltaComparisonService` plus `run_snapshot_diff` (what Compare uses), `project_health` and `triage.TriageStore`. No SQL, nothing under `repository/`, so there is still one read path and the answers match the UI.
- **Small answers.** Failure messages are cut to their first line, lists to 50, and logs and traces are never returned, so a call costs an agent a few hundred tokens instead of a log.
- **Read-only by default.** Write tools (`mark_failure_cause`) are registered only with `--allow-writes`; without it they don't exist, so an agent can't call them. Every tool carries MCP's read-only or idempotent hints.
- **Optional dependency.** The official `mcp` SDK is the `[mcp]` extra and is imported only by `build_server`; `import qasight` and the tool functions don't need it. The SDK renamed `FastMCP` to `MCPServer` in 2.0; the server takes whichever is installed (`mcp>=1.23`).
- **Errors are tool errors.** A bad argument or an unknown run, cycle or test raises `ToolInputError`, which becomes the SDK's `ToolError`, so the agent reads the reason and no traceback is logged.

## Consequences

- An agent can go from "CI is red" to the failing tests, whether they're new or flaky, their history, a rerun command and a cause, without a browser or a log.
- Not done: reading a team server over its HTTP API (`--server` / `--token`). Mapping each tool onto API responses would be a second read path; for now run `qasight mcp` where the team server's database is reachable. Also not done: an HTTP transport, quarantine from a tool (quarantine lives in `qasight.yaml`, a file the agent can edit itself), and role checks on write tools (the server runs as the local user).
- Commands, client config and the tool list: [Command Reference § `qasight mcp`](../CLI%20Commands/Command%20Reference.md#qasight-mcp).
