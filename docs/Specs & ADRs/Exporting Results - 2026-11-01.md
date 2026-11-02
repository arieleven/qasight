---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-01
---

# Exporting Results - 2026-11-01

## Context

Taltal: "there is no easy way to extract results". The dashboard's main use is good-looking data for senior managers, and the only copy button in the UI was the rerun command on the failing tests card. Research (Grafana, Datadog, Looker, ReportPortal, TestRail, Allure 3) found one pattern everywhere: a PDF of the dashboard rendered by a headless browser, a layout choice, and delivery on a schedule to email, Slack or Teams. Test tools stop at a one-off PDF of one run; none ships slides. On 2026-11-01 Taltal chose to build all four phases of the plan.

## Decision

One renderer for every format: the UI's pages, with the data the API already computes. No second hand-made template per format.

1. **Copy anything.** Every titled `Card` has a Copy menu (`components/ui/CopyMenu.tsx`, helpers in `lib/clipboard.ts`): copy as text, copy as table (`text/html` with an inline-styled table plus tab-separated `text/plain`, so it pastes as a table in email, Slack, Confluence, Notion and Google Docs, and as cells in a spreadsheet), download CSV, copy or download a chart as a PNG (the SVG with its computed paint inlined, drawn at 2x on a white background), and a link to the card (`#card-<title>`). Cards opt out with `copy={false}`. The run page has "Copy summary": `GET /api/v1/runs/{id}/markdown`, the same text as `testo report markdown` without the hidden PR-comment marker.
2. **Manager report page and PDF.** `GET /api/v1/report?period=day|week|month|quarter&cycle=...` (`testo_core/services/manager_report.py`) builds one payload: headline numbers against the period before (test health score, pass rate, green builds, line coverage or what needs attention), a few summary sentences, a day-by-day trend, one row per cycle, still failing, turned flaky, fixed, got slower, and the health score's findings. It reuses `run_digest.build_digest` (now with a `days` override) and `project_health`, so the numbers match `testo notify --digest` and the Project health page. The `/report` page (`features/report/`) shows it as a white A4-shaped document (`ReportDocument`) with its own fixed palette, so it looks the same on screen, printed, copied as an image or rendered by `testo export`. "Save as PDF" calls `window.print()`; print CSS hides the app's navigation, removes the dark background and keeps sections whole. Every section has the Copy menu. The static demo serves `report/<period>.json` and `report/<period>/<cycle>.json`.
3. **Files from the server and the CLI.** `testo export FORMAT` and `GET /api/v1/report/export?format=` (the Report page's Download menu) share `services/report_export.py`. The single-file HTML is the built UI (`testo_core/ui_bundle.py` finds it, shared with `testo_api/ui.py`) with its script and styles inlined, Google Fonts links dropped and the report baked in as `window.__TESTO_REPORT__`; `main.tsx` then renders only `ReportDocument` (`features/report/standalone.tsx`), no router or fetches. PDF and PNG are that file rendered by headless Chromium through Playwright, the way Grafana, Datadog and Looker render dashboard PDFs: PDF in print media on A4, PNG of the report element at 2x with the Copy menus hidden (`?capture=1`). `TESTO_CHROMIUM` points at a system Chrome instead of Playwright's download. `testo notify --digest week --to email` emails the report (summary in the body, the file attached, `--attach pdf|pptx|xlsx|html|png|none`) over SMTP (`services/report_email.py`, `TESTO_SMTP_*`), since chat webhooks can't carry files.
4. **Data and slides.** `services/report_office.py`: an Excel workbook (Summary, Trend with a native line chart, Cycles, Tests, Findings; header row, filters, frozen panes) and a 16:9 PowerPoint deck (title, headline numbers, pass-rate line and runs-per-day stacked column charts as native charts, cycles table, what needs attention, fixed and what to fix first), plus CSV (the cycles table, UTF-8 with a BOM for Excel) and JSON.

The `export` extra (`playwright`, `python-pptx`, `openpyxl`) holds the heavy parts; HTML, CSV and JSON need none of it. The team server image installs python-pptx and openpyxl, and Chromium only with `--build-arg TESTO_REPORT_PDF=1` ([Team Server § Reports for managers](../Processes%20&%20Guides/Team%20Server.md#reports-for-managers)).

## Consequences

- The clipboard needs a secure context (HTTPS or `localhost`); elsewhere the menu falls back to plain text, and says so when even that is refused.
- Chart images work for SVG charts. Bar charts drawn with plain `div`s are copied as text or table, not as images.
- The static demo answers `/runs/{id}/markdown` from `runs/<id>/markdown.json`, written by `scripts/export_static_site.py`, and `/report` from `report/<period>[/<cycle>].json`. It has no Download menu (no server); "Save as PDF" prints.
- One look everywhere: the report's palette is fixed colours (`features/report/palette.ts`, mirrored in `report_office.py`), not the app's dark theme, so screen, print, PNG and slides match. A change to the report's content goes in `manager_report.py` once and shows up in every format.
- PDF and PNG take a few seconds and a Chromium process per request; the API renders them in its worker threads, fine for people clicking Download, not for a public endpoint under load.
