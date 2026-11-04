---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-04
related: "UI Design Review - 2026-10-31, UI Restyle - 2026-10-19, Product Review"
---

# UI Review Fixes - 2026-11-01

[Index](../Index.md) · builds the proposals from the [UI Design Review - 2026-10-31](UI%20Design%20Review%20-%202026-10-31.md) that Taltal picked.

## Context

The design review scored the web UI 7.5 / 10 and left seven findings as proposals. Taltal picked five on 2026-11-01 ("do all 5"): the Cycles grid shows no result per cycle (U1), Compare hides which tests changed (U2), every run of a cycle has the same label (U4), the test page's charts were unfinished (U6), and some cards judge or sit empty (U8). The "got slower" noise (U7) and the raw artifact list (U11) stay open.

## Decision

Two pull requests, both frontend-led, with the smallest API change that lets a page show what it needs in one request.

### Cycles page shows each cycle's latest result (U1)

- `GET /api/v1/cycles` items carry `latest_run` (`run_id`, `created_at`, `status`, `returncode`, `health_pct`, `total_tests`, `passed`, `failed`, `branch`, `commit`), read with the one `latest_run_per_cycle()` query the route already made. No per-cycle request from the UI.
- Each card shows a status pill, the time of the run as a link ("3 h ago", full date on hover), "80% passed", "2 failing" in red, `branch @ commit`, and a pass-rate bar. A cycle that never ran says "No runs recorded yet." The Run button is secondary so the result is what stands out.

### Run labels say when and from where (U4)

- `formatRelativeTime` ("just now", "3 min ago", "5 h ago", "3 days ago", then the date) and `formatProvenance` ("main @ 9c23517") in `frontend/src/lib/format.ts`.
- `RunLabel` takes `relative`; the Dashboard and Runs pages pass it. Run list items and dashboard recent runs carry `commit` next to `branch` (`RunListItem.commit`, `DashboardRecentRunItem.commit`), read from the stored session.

### Compare answers "which tests?" right under the summary (U2)

- The test-level list moves from under the four tables to right under the summary card and is open by default. The query runs when the section is open.
- "4 regressions" and "2 improvements" in the summary are buttons: they set the list's filter (regression, fix) and scroll to it. Each filter chip shows its count; each test links to its history page.
- "Test time (sum)" is dropped from the Performance table when it equals the wall duration in both runs (a single-stage run), so the table does not repeat a row.

### Test page charts (U6)

- Duration: bars fill the card, each bar is a link to its run (tooltip and accessible name carry the time, outcome and duration), three y ticks, the run date under every bar or every nth so the axis stays readable.
- Flakiness: a line of flip rate per run, 0 to 100 %, with points and a 50 % guide, and the same sentence under it. The chart's text alternative is unchanged.
- Test outcomes use `StatusPill`, which gains `broken` and `skipped`, so the page says "Passed" / "Failed" like every list. Each run row carries its short id so it can be matched with the "Last failure" card.

### Cards that judge, cards that are empty (U8)

- Test mix is shown only when at least two tiers hold tests. An ingested run has one tier (`qasight ingest` takes one `--tier`), so there was nothing to judge. A failed pyramid request still shows its error.
- An empty Reports card folds into the Details list as a "Reports" row. A recorded-only cycle's page drops the Stages card: its one stage has nothing to configure and the header already names it.

## Consequences

- `GET /api/v1/cycles` grows one optional field; `GET /api/v1/runs` and the dashboard payload grow `commit`. The OpenAPI file and `frontend/src/lib/api-schema.ts` are regenerated; nothing is removed.
- The Compare page issues the case-changes request on every comparison instead of only when someone opened the section. It is the per-test diff the page exists for, and it was computed on demand before too.
- Remaining from the review: U7 (cap and sort "got slower" lists, noise floor check on real data) and U11 (group artifacts by stage, link the report ones).

## Pull requests

- [#237](https://github.com/arieleven/qasight/pull/237): U1 and U4.
- [#243](https://github.com/arieleven/qasight/pull/243): U2, U6 and U8, plus this note.
