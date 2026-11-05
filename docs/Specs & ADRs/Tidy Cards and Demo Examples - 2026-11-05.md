---
type: spec
status: current
created: 2026-11-05
updated: 2026-11-05
related: "UI Review Fixes - 2026-11-01, GitLab Pages Demo"
---

# Tidy Cards and Demo Examples - 2026-11-05

[Index](../Index.md) · Taltal's ask on 2026-11-05: make Needs attention open and close on click, show only the last 3 recent runs, give every other messy element the same treatment, and give the live demo's releases and AI features examples, even fake ones, marked as fake.

## Context

The dashboard and the run and cycle pages showed every row they had: eight recent runs, every failing test with its full message, every broken test, every stage's config. On the live demo the AI cards (failure summary, AI review, Ask) said "can't run here" and Releases was empty, because the demo has no AI key and its runs record no `--release`.

## Decision

* **Two shared pieces in `frontend/src/components/ui/`.** `Card` takes `collapsible` (click the title to open or close; remembered per card in `localStorage` key `qasight.cardOpen`), `defaultOpen` and `collapsedSummary` (one line shown while closed). `useShowMore` / `ShowMoreButton` show the first `FIRST_ROWS` (3) rows of a list with "Show all N" / "Show fewer".
* **Where they are used.** Dashboard: Needs attention collapsible (open by default), its lists 3 rows each; Recent runs collapsible, 3 rows and an All runs link. Run page: failing tests 3 rows; Test mix, Details and Artifacts start closed. Cycle page: flaky and broken tests 3 rows; Stages starts closed. Release page: failing and fixed tests 3 rows. The Report page is left whole: it is a document people print and save as PDF.
* **Made-up examples, demo only.** `qasight_api/demo_examples.py` writes, from the demo's real runs and without calling any AI, an AI failure summary per failed run, an AI review, two Ask turns and two pretend releases. Only `scripts/export_static_site.py` (the demo) does this; `qasight export-site` never does. Each is marked (`provider: "demo-example"`, manifest `examples`) and the UI says "Fake example for the live demo" next to it. Details: [GitLab Pages Demo](../Processes%20&%20Guides/GitLab%20Pages%20Demo.md#what-visitors-see).

## Consequences

* A long list never pushes the next card off screen; everything is one click away.
* A card a viewer closed stays closed in that browser, including Needs attention. Its closed line still says how many cycles are red.
* Example releases come from patching the releases service's grouping for the length of the export, so the cached demo history stays real. When a real summary, review or release exists, the example is not written.
