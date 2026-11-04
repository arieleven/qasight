---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-04
---

# Free Copies of Paid Features - 2026-11-01

## Context

[Paid Features to Offer Free - 2026-10-31](../Roadmap%20&%20Strategy/Paid%20Features%20to%20Offer%20Free%20-%202026-10-31.md) ranked twelve features that competitors keep behind a paid plan. On 2026-11-01 Taltal picked five to build: F1 coverage history and coverage on the pull request comment, F2 a weekly digest, F5 hiding secrets before anything is stored, F6 backup and restore, and F9 previous failures first. The note's rules apply: only the ideas are copied, never code, assets or the competitors' feature names.

## Decision

Each feature uses the paths that already exist. There is no new service and no new table, and every feature except redaction is off until asked for.

### F5: secrets hidden before a run is stored

`qasight/security/redaction.py` used to clean only what went to the AI. `redact_run_artifacts()` now rewrites the text files a run wrote (`.json`, `.xml`, `.log`, `.txt`, `.html`, `.trx`, ... up to 50 MB; binaries, symlinks and non-UTF-8 files are left alone). `run_plan()` calls it once, after the last stage and before persistence. So `qasight run`, `qasight ingest`, `POST /api/v1/ingest` and `pytest --qasight` are all covered, and reporters only see the cleaned files.

- The built-in patterns cover private key blocks, `sk-…`, `api_key=`/`token=`/`secret=`, `password=`, `Bearer …`, AWS `AKIA…`/`ASIA…`, Google `AIza…`, GitHub `ghp_…`/`github_pat_…`, GitLab `glpat-…`, Slack `xox…-` tokens and hook URLs, JWTs, and `user:password@` in URLs (scheme and host are kept).
- The value patterns stop at quotes and backslashes, so a rewritten JSON file stays valid.
- `TESTO_REDACT_PATTERNS` adds your own regular expressions, one per line. An invalid one is skipped with a warning. `TESTO_REDACT=0` turns redaction off.
- It is on by default: a secret in stored history is worse than a few `***REDACTED***` in a log.

### F6: `qasight backup` and `qasight restore`

`qasight/history/backup.py` writes one `.tar.gz` containing a manifest, one JSONL file per table (read through SQLAlchemy) and the run snapshots under `static/history/`.

- Because the tables are JSONL rather than a database dump, one backup moves between SQLite, Postgres and MySQL. The note had proposed a file copy plus `pg_dump`; this works for every database the project supports.
- Values that aren't JSON are tagged (`$dt`, `$date`, `$uuid`, `$b64`), and enums are stored by name.
- Restore refuses a database that already has runs unless `--replace` is given. It writes only the columns today's tables have, so an older backup still restores.
- Archive members are checked before extraction (no absolute paths, no `..`).
- The AI settings file is never included. The manifest notes that `user_account` holds password hashes.

### F9: `pytest --qasight-failed-first`

`services/failed_first.py` ranks each test from the cycle's history:

1. failed in the last run;
2. flaky;
3. failed in one of the last 20 runs;
4. everything else.

The pytest plugin then does a stable sort, after `--qasight-affected` and `--qasight-shard`. History is read the same way `--qasight-shard` reads it (`services/test_split.load_test_items`): locally, or from `/api/v1/analytics/tests` with `--qasight-server`. That makes the order the team's history, not one machine's cache. The pytest-xdist controller loads the ranks once and passes them to each worker through `workerinput`. If no history can be read, pytest's own order is kept and the summary line says why.

### F2: `qasight notify --digest week|day`

`services/run_digest.py` builds one message per period from the stored runs and their snapshots. For each cycle it shows:

- runs and green builds, against the period before;
- test pass rate and line coverage, against the period before;
- tests that turned flaky (`flaky_service.classify`, the same rule as `qasight flaky`);
- tests still failing;
- tests fixed since the previous period's last run;
- the five tests that got slower (median passed time, above the 20 ms noise floor).

It is sent as Slack Block Kit or a Teams Adaptive Card to the webhooks the per-run alerts already use. QASight has no scheduler, so a cron line or a scheduled CI job runs it. A period with no runs sends nothing.

### F1: coverage with each run and on the pull request comment

`reporting/coverage_import.py` recognises a report by its content, as the result importers do: Cobertura XML, JaCoCo XML (the report's `LINE` counter), LCOV (`LF`/`LH`), and Go cover profiles (statements, with blocks repeated across test binaries counted once). Several reports are added together. DTDs are stripped and entities are refused.

How coverage gets in:

- `qasight run --coverage FILE` reads the file after the stages finish. A missing file only logs a warning, so a crashed test run is still recorded.
- `qasight ingest --coverage FILE` checks the file before recording, so a typo is exit `2`.
- The GitHub Action has a `coverage` input.

Where it is kept and shown:

- Only the totals are stored, as `coverage_pct`, `coverage_covered` and `coverage_total` in the run record's metadata, the same place CI provenance goes.
- With `--server`, the client sends the covered and total counts. The server checks them and computes the percentage itself.
- `CompletedRunView` and `GET /api/v1/runs/{id}` expose the three numbers, and the run page shows them.
- `qasight report markdown` (job summary and PR comment) adds `coverage 81.2% (▼ 0.4)` against the same baseline as its test changes, and the digest adds it per cycle.

## Consequences

- The five features are free and need no new service. Redaction is the only behaviour change for existing users. It changes stored files, never test outcomes, and `TESTO_REDACT=0` restores the old behaviour.
- F1 is narrower than the note's sketch. There is no per-file coverage and no coverage trend chart on the cycle page yet. The stored per-run totals are what such a chart would read, and diff coverage (changed lines only) can follow on top of what `qasight affected` knows about changed files.
- Coverage lives in run metadata rather than a column. That fits the current read side; it moves to a real column with the rest when the database migrations planned in the architecture review land.
- Redaction patterns are a best effort. A secret in an unusual shape still needs `TESTO_REDACT_PATTERNS`.
- Restoring a big history goes through the ORM in batches of 500 rows. That is slower than a native dump, but works the same on every database.

Builds on [Alerts Where Developers Look - 2026-10-22](Alerts%20Where%20Developers%20Look%20-%202026-10-22.md), [Ingest and Team Server - 2026-10-22](Ingest%20and%20Team%20Server%20-%202026-10-22.md) and [Affected Test Selection - 2026-10-30](Affected%20Test%20Selection%20-%202026-10-30.md).
