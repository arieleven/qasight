---
type: spec
status: current
created: 2026-11-12
updated: 2026-11-12
---

# Menu by Question - 2026-11-12

## Context

The sidebar had grown to thirteen flat entries plus a "Tools" group. Taltal found three problems:

- **Two scores, and names that don't say what is measured.** "Project health" scores only the *tests* (passing, green builds, stable, speed, hygiene, pyramid; see [Project Health - 2026-10-26](Project%20Health%20-%202026-10-26.md)). "Scorecard" grades the *code* of a version (linters, security, API, web and load tools; see [Release Scorecard - 2026-11-12](Release%20Scorecard%20-%202026-11-12.md)). "Project health" read as the overall score.
- **Overlap.** The dashboard already showed the health gauge, and Releases and Scorecard were both "per version" on separate pages.
- **"Tools" was a bin.** Quick run is an action, Resources and AI settings are settings, Try a file is onboarding.

## Decision

Group the menu by the question each page answers (`NAV_GROUPS` in `frontend/src/app/AppShell.tsx`):

| Group | Entries | What moved there |
|-------|---------|------------------|
| (top) | Overview | Was "Dashboard". Shows Test health and Code quality side by side, each named for what it measures. |
| Tests | Runs, Cycles, Test health | Compare is a button on Runs; Quick run a button on Cycles. "Project health" is now **Test health** (`/test-health`). |
| Releases | Releases | Two tabs: Test results (`/releases`) and **Code quality** (`/code-quality`, was "Scorecard"). A release's page shows its code grade. |
| Share | Report, Ask | |
| Settings | Resources, AI settings | Try a file moved to the welcome page, the sidebar help card and the footer. |

Thirteen entries became nine. A page without its own entry (Compare, Quick run, Code quality) keeps its parent highlighted and shows it in the breadcrumb.

Old addresses keep working: `/health` and `/scorecard` redirect to `/test-health` and `/code-quality` with their query string. `/compare`, `/quick-run` and `/try` are unchanged. The API is unchanged (`/api/v1/project-health`, `/api/v1/scorecard`).

## Consequences

- One word per score: "Test health" for tests, "Code quality" for code. New UI text should keep to these names.
- The CLI (`qasight scorecard`) and the API keep their names; only the UI labels changed.
- README screenshots still show the old menu until they are retaken.
