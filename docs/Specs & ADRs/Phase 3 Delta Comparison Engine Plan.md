---
type: spec
status: current
created: 2026-06-25
updated: 2026-11-04
---

# Phase 3 Delta Comparison Engine Plan

<!-- source: notion https://www.notion.so/354d95cd031280249689fa3390e43594 -->

## Strategy (WHY)

QA teams need side-by-side comparison of two runs (reliability + performance) with deterministic regression/improvement labels — especially for load and multi-stage cycles.

## Implementation (HOW — use these as source of truth)

| Layer | Detail |
|-------|--------|
| Metric semantics | [Delta Comparison Policy](Delta%20Comparison%20Policy.md) |
| Core service | `qasight/services/delta_service.py` |
| CLI | [Command Reference § `qasight diff` / `qasight summary`](../CLI%20Commands/Command%20Reference.md#qasight-diff--qasight-summary) |
| API | `GET /api/v1/analytics/delta` |
| React | `frontend/src/features/compare/` |

Do not duplicate the direction table here — it lives in [Delta Comparison Policy](Delta%20Comparison%20Policy.md).

## Release gate

[Release Checklist - Phase 3 Delta Engine](../Archive/Release%20Checklist%20-%20Phase%203%20Delta%20Engine.md)

---
**Context & Links:** [Delta Comparison Policy](Delta%20Comparison%20Policy.md), [Architecture Overview](../Architecture/Architecture%20Overview.md), [Product Roadmap § Phase 3: Enterprise UI & Analytics](../Roadmap%20&%20Strategy/Product%20Roadmap.md#how-it-got-here)
