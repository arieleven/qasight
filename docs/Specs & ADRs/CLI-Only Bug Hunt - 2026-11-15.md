---
type: spec
status: current
created: 2026-11-15
updated: 2026-11-15
---

# CLI-Only Bug Hunt - 2026-11-15

## Context

Releases are reviewed by clicking through the web UI and the Pages demo. This hunt looked for what that review can't see: commands and flags that only exist in the CLI, and places where the CLI and the UI disagree. Every command in [Command Reference](../CLI%20Commands/Command%20Reference.md) was run against `qasight demo` history, the repository's sample cycles, a project with only a `testosterone.yaml` and the old `testo` command, and the API (`qasight ui`) on the same database. MCP was driven over real stdio (`initialize`, `tools/list`, every tool).

## Decision

Fixed in pull request #339:

| # | Bug | Fix |
|---|---|---|
| 1 | `qasight scorecard scan` read Ruff's absolute paths from the folders *above* the repository, so a checkout under `/build/`, `~/out/`, `~/test/`, `docs/` or `vendor/` had every finding dropped as generated or test code and scored 100, passing the release gate. The same absolute paths made finding fingerprints differ between checkouts, so `--no-new` saw old findings as new. | Findings are read relative to the scanned repository (`scan_root` in `qasight/services/scorecard/paths.py`). |
| 2 | `qasight run` and `qasight ingest` printed "Recorded run &lt;id&gt;" with the report archive's id. The UI, the API, MCP and `qasight ai summary --run-id` only know the run id, so the printed id 404'd everywhere. | The run id is printed. |
| 3 | `qasight doctor` skipped the database check when there was no `qasight.yaml`, even with `DATABASE_URL` set. | `DATABASE_URL` is probed without a config. |

Taltal approved all five proposals on 2026-11-15 ("this tool is CLI first: most people will run it through Claude in the background"), and they are built in the same pull request:

| # | Finding | Built |
|---|---|---|
| P1 | `qasight diff` and `qasight report list` use archive ids; an id from the UI or MCP is refused ("archive ids were not found"). | `qasight diff` accepts run ids; a new run's archive takes the run's id, so one id names it everywhere. |
| P2 | `qasight try` with no files ingests every result file it finds as one run. With stale files beside the new one (the demo folder has eight), counts multiply and the comment names the wrong regression. | Older JUnit files of the same tests are left out, and named (also for `qasight ingest` with no files, which had the same bug and records it). |
| P3 | `qasight doctor` fails (exit 2) without a `qasight.yaml`, though zero-config `ingest` and `ui` are supported. | INFO, exit 0; a config named with `-c` that is missing, or a broken one, still fails. |
| P4 | The scorecard summary says "No lint issues" when findings exist but are all in tests; the "not counted" number only shows in JSON. | The summary adds "; 3 more in tests, vendored, generated code or docs, not counted". |
| P5 | `qasight export --out report.pdf` errors with "Missing argument 'fmt'". | The format comes from the extension. |

Smaller: the demo's hint prints `TESTO_HOME`, `.testo` and `testo_history.db` (the `QASIGHT_*` aliases work), and in the source checkout `qasight run` can't draw the Allure HTML until `npm install` at the root (the run still passes).

## Consequences

- CLI test coverage was broad already: every command has `CliRunner` tests, and CI runs the GitHub Action, `qasight ingest --server`, `qasight run`, `export-site` and `scorecard scan` end to end (Pages demo, release gate). The new `cli end-to-end` job (`scripts/cli_e2e.py`) adds what none covered: `doctor` without a config, `try`, the id `ingest` prints, `qasight mcp` over real stdio, `diff` on run ids, and `export` in every format including PDF and PNG.
- QASight is CLI-first: most people run it through a coding agent in the background, so CLI paths get end-to-end checks of their own, not only the UI review.
- The CLI's `summary` counts regressions per test, while the dashboard's reliability roll-up counts metrics, so the two numbers differ by design.
