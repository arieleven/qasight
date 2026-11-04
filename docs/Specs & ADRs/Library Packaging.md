---
type: spec
status: current
created: 2026-10-07
updated: 2026-11-04
---

# Library Packaging

## Decision

Ship the orchestrator as an installable library (`qasight` on PyPI, import `qasight`) so the CLI, CI wrappers, the API and custom scripts share one engine instead of each needing an app checkout.

## Implementation

| Item | Location |
|------|----------|
| Package metadata | `pyproject.toml`: `name = "qasight"` |
| Public API | `qasight/__init__.py` |
| CLI entry | `qasight` console script (`qasight.cli.app:main`) |
| API entry | `qasight-api` console script (`qasight_api`) |
| Dev install | `pip install -e '.[dev]'` |

Run history is always saved (SQLite by default), so `sqlmodel` is a core dependency; the `[db]` extra only adds the Postgres driver and `tomli-w`. The `package` CI job installs the built wheel with no extras and runs a cycle to keep it that way.

An installed package writes nothing next to its own code: run data goes to `<cwd>/.testo/` or `$TESTO_HOME`, and a source checkout keeps using the repository root ([Architecture Overview § Artifact layout](../Architecture/Architecture%20Overview.md#artifact-layout)). Up to 1.1.1 it was always relative to the package directory, so a `pip install` wrote `static/` into `site-packages`.

FastAPI (`qasight_api`) and the React frontend are **optional consumers**: `qasight run` must not need them. See [Architecture Overview § Adjacent packages (same repo)](../Architecture/Architecture%20Overview.md#adjacent-packages-same-repo).

Publishing: [Publishing to PyPI](../Processes%20&%20Guides/Publishing%20to%20PyPI.md). Commands: [Command Reference](../CLI%20Commands/Command%20Reference.md).
