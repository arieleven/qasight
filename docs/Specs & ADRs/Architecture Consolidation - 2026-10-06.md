---
type: spec
status: current
created: 2026-10-06
updated: 2026-11-04
---

# Architecture Consolidation - 2026-10-06

## Context

Before this batch the CLI and the API ran cycles through two different code paths, the frontend's API types were written by hand, run history was read through several ad-hoc helpers, and mypy and `ruff format` were advisory. The project is meant to be read by other engineers, so the structure had to be explainable in one diagram.

## Decision

| Change | Pull request |
|--------|--------------|
| Accurate `ARCHITECTURE.md` with a system diagram; frontend CI job; generated artifacts untracked | [#61](https://github.com/arieleven/qasight/pull/61) |
| The cycle use case moves into `services/cycle_run.CycleRunService`, shared by CLI and API | [#60](https://github.com/arieleven/qasight/pull/60) |
| The legacy execution stack is retired; every run goes through the cycle engine | [#62](https://github.com/arieleven/qasight/pull/62) |
| Frontend API types are generated from FastAPI's OpenAPI schema and typechecked in CI | [#63](https://github.com/arieleven/qasight/pull/63) |
| One storage boundary for run history (`qasight/history/`) | [#65](https://github.com/arieleven/qasight/pull/65) |
| mypy backlog cleared; mypy and `ruff format` are blocking in CI | [#64](https://github.com/arieleven/qasight/pull/64) |
| `changelog-on-main` skips instead of failing when bot secrets are missing | [#66](https://github.com/arieleven/qasight/pull/66) |

## Consequences

- One engine: `config/loader.py` → `config/resolver.py` → `engine/orchestrator.run_plan()` → `engine/executor.run_stage()`, entered only through `CycleRunService`. See [Architecture Overview](../Architecture/Architecture%20Overview.md).
- A backend schema change that the frontend doesn't regenerate for fails CI.
- The 2026-10-07 [Interview Readiness Fixes](Interview%20Readiness%20Fixes%20-%202026-10-07.md) build on this base.
