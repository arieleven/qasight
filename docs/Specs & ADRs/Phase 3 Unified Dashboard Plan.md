---
type: spec
status: current
created: 2026-06-25
updated: 2026-11-04
---

# Phase 3 Unified Dashboard Plan

<!-- source: notion https://www.notion.so/354d95cd0312807d821bfe7dcbe1c5ba -->

## Strategy (WHY)

Consolidate scattered Allure and framework report links into one health-first overview with drill-down to raw HTML and compare/history flows.

## Implementation (HOW)

| Layer | Detail |
|-------|--------|
| Aggregation | `qasight/services/dashboard_service.py` |
| API | `GET /api/v1/dashboard/overview` |
| UI | `frontend/src/features/dashboard/DashboardPage.tsx` — primary `/` route |
| Delta semantics | Reuses [Delta Comparison Policy](Delta%20Comparison%20Policy.md) classifications in rollups |

## Release gate

[Release Checklist - Phase 3 Unified Dashboard](../Archive/Release%20Checklist%20-%20Phase%203%20Unified%20Dashboard.md) — run after [Release Checklist - Phase 3 Frontend Migration](../Archive/Release%20Checklist%20-%20Phase%203%20Frontend%20Migration.md) and [Release Checklist - Phase 3 Delta Engine](../Archive/Release%20Checklist%20-%20Phase%203%20Delta%20Engine.md).

---
**Context & Links:** [Architecture Overview](../Architecture/Architecture%20Overview.md), [QA Strategies § How results are logged and surfaced](../Testing%20Workflows/QA%20Strategies.md#how-results-are-logged-and-surfaced)
