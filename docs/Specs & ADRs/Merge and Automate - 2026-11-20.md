---
type: spec
status: current
created: 2026-11-20
updated: 2026-11-20
---

# Merge and Automate - 2026-11-20

## Context

After the [Focus Review](Focus%20Review%20-%202026-11-20.md) cut what QASight shows first, Taltal asked for the next step in the style of recent Anthropic releases: few new features, pages and buttons merged into one, and features that happen by themselves. QASight is CLI-first and mostly driven by coding agents, so most of the work is in the CLI. The review (13 items, ranked by impact against effort) is in the project's files, `merge-and-automate/qasight-merge-automate-2026-11-20.md`.

## Decision

Make the common path need no flags, and fold commands and pages into the one that answers the question. Nothing is removed: every old name keeps working.

| # | Change | Where |
|---|--------|-------|
| 1 | `run` and `ingest` end with what's new against the previous run (new failures, still failing, fixed, newly flaky, slower); `--ci` puts it on `plan_finished` as `news` | `qasight/services/run_news.py` |
| 2 | The project and its history are found from any subfolder, like git | `qasight/project_root.py` |
| 3 | `--cycle` is never required: env, the only cycle, or the newest run's | `qasight/cli/cycle_pick.py` |
| 4 | One `--json` switch; coding agents (`CLAUDECODE`, `QASIGHT_AGENT`) get JSON by themselves | `qasight/cli/output_mode.py` |
| 5 | In CI, `run` and `ingest` publish the result themselves; the Action's advanced inputs say so | `qasight/services/ci_publish.py` |
| 6 | `summary [RUN] --against previous|last-green|<branch>|<id>` absorbs `diff`; `qasight runs` lists, shows and deletes runs | `qasight/cli/commands/diff_cli.py`, `runs.py` |
| 7 | `export` writes everything: the report formats plus `site`, `badge`, `markdown`, `annotations` | `qasight/cli/commands/export_cmd.py` |
| 8 | Project settings in `qasight.yaml` (`settings:`), `QASIGHT_*` read as `TESTO_*` | `qasight/project_settings.py` |
| 9 | `init` imports the last 10 CI runs' results from GitHub | `qasight/cli/commands/init_cmd.py` |
| 10 | One Settings menu entry in the UI, with Project, AI and Resources tabs | `frontend/src/features/settings/` |
| 11 | The Overview's Test health score opens in place with what drives it, what to fix and each cycle; the menu loses its Test health entry (`/test-health` stays) | `frontend/src/features/dashboard/DashboardPage.tsx` |
| 12 | `doctor` checks the project folder, unknown keys (`--strict`), each cycle and the settings in effect; `cycles` becomes its alias | `qasight/cli/commands/doctor.py` |
| 13 | `flaky --quarantine` quarantines what it finds | `qasight/cli/commands/flaky.py` |

Commands and flags: [Command Reference](../CLI%20Commands/Command%20Reference.md#what-happens-without-a-flag).

## Consequences

- An agent or a person can type `qasight run -- pytest`, then `qasight summary` or `qasight flaky` with no ids or cycle names.
- A CI job needs one step: `qasight run` or `qasight ingest`. A separate `qasight ci publish` step still works and doesn't post twice.
- `plan_finished` stays the last NDJSON event; `news` is an added field, so readers that ignore unknown keys are unaffected.
- `qasight cycles`, `diff`, `export-site` and `badge` are hidden aliases (listed by `qasight help --all`); scripts that call them keep working.
- Defaults that act by themselves each have an off switch (`TESTO_PUBLISH=0`, `TESTO_RUN_NEWS=0`, `QASIGHT_OUTPUT=text`, `--no-backfill`, `--cycle`).
