---
type: spec
status: current
created: 2026-11-20
updated: 2026-11-20
---

# Stress Test - 2026-11-20

[Specs & ADRs](README.md) · [Team Server](../Processes%20&%20Guides/Team%20Server.md)

## Context

Taltal asked where QASight slows down or fails under load. The Benchmark guide (PR #340) already measured one client at a time; this exploration pushed the paths a team server and a CLI user hit hardest: big and many uploads, concurrent readers, several workers, the CLI writing next to the server, Postgres, memory and disk.

Setup: a 4-CPU, 15 GB Linux container, Python 3.13, SQLite 3.53 and Postgres 16 on the same machine, `scripts/benchmark.py` from PR #340 (Surpass ReportPortal), and probe scripts for the rest. Numbers are from 50 runs x 5,000 tests (250,000 results) unless a row says otherwise.

## Findings, ranked

| # | Impact | What happens under stress | Measured | Status |
| --- | --- | --- | --- | --- |
| S1 | High | A run's failures, flaky tests and test search collapse when several people open them at once on SQLite. The `sqlite3` module releases the GIL on every row, so threads reading 100,000 rows each hand the lock back and forth per row | 8 at once: 190 s each (2 s alone). Raw `sqlite3`: 8 threads 156 s, same reads in turn 1.7 s, 8 processes 0.35 s | Fixed: one SQLite read of a run's rows at a time per process, plain rows instead of ORM objects (8 at once: 8 to 10 s each), then P1 (8 at once: 0.1 s each) |
| S2 | High | Those three pages are still about 1 s each for one person, because each request rebuilds 20 runs x 5,000 test rows in Python | 0.73 s / 1.22 s / 1.23 s p50 after S1 (were 1.98 / 2.55 / 2.61 s) | Fixed by P1: first request after a run 0.05 / 0.84 / 0.48 s, then 15 / 9 / 6 ms |
| S2b | High | On Postgres the same three pages cost the same as on SQLite after S1, and 8 at once take as long as 8 in turn: the time is Python building rows, not the database | 250,000 results, 4 workers: 0.70 / 1.25 / 1.31 s p50; 8 at once 7.7 / 11.2 / 10.9 s each | Fixed by P1 (the same code path) |
| S3 | High | Uploads crawl when the server is also answering reads (one worker). An upload is CPU-bound Python plus ~300,000 small file operations per 50,000 tests, and every file operation waits for the GIL behind the readers | 8 uploads x 5,000 tests: 16 s alone, 32 s with 4 clients reading twice a second, 6 uploads in 10 min with 4 clients reading non-stop | Fixed by P2: 8 uploads next to 4 clients reading non-stop take 51 s, reads p50 15 ms |
| S4 | Medium | Recording an upload does each test several times over: JUnit becomes one Allure JSON file per test, then those files are parsed twice for metrics, twice for stage health, read again for redaction, `load_cases`, the history copy and the report zip | 50,000-test upload: 50 s under the profiler. DB insert through ORM 10 s, zip 9 s, parsing 5.5 s, redaction 5.5 s, copy 4 s | Partly fixed by P3: 43 s to 36 s without the profiler |
| S5 | Medium | Disk use is about ten times the uploaded size: every test is its own file in `static/history/<run>/`, plus a zip of the same files and the DB rows | 5,000-test run: 0.6 MB JUnit, 2.3 MB of JSON, 21 MB on disk (4 KB blocks). 1M results is about 1M files and 4 GB | Proposal P4 |
| S6 | Medium | One upload at a time per process (`_INGEST_LOCK`); a 100,000-test upload holds it for about a minute, and the CLI client gives up after 120 s while the server keeps recording, so a CI retry records the run twice | 100,000 tests (11 MB): 58 s, peak 770 MB RSS | P2 moves it off the API's threads; P5 open |
| S7 | Medium | `POST /api/v1/ingest` read every file of an upload into memory before checking the 100 MB total | One 475 MB request (25 files under 20 MB): server 111 MB to 590 MB | Fixed: checked while reading |
| S8 | Medium | Memory after a burst stays high: Python keeps freed memory | 8 heavy page loads at once: peak 970 MB, 590 MB after. Steady ingests don't grow it (156 MB after 10 and after 20) | Better after P1: 8 heavy page loads at once peak at 262 MB. A big upload's memory now goes with the upload worker, which restarts every 50 uploads |
| S9 | Medium | With several workers (`TESTO_WORKERS`), a scorecard scan lived in one worker's memory: polls hit other workers and got 404, and each worker allowed its own scan | 12 of 20 polls 404; 3 scans ran at once | Fixed in PR #340 (scan state in the DB) |
| S10 | Low | The ReportPortal endpoint read the whole request body before checking its 32 MB limit | (same class as S7) | Fixed in PR #340 |
| S11 | Low | A scanner that times out is killed, but processes it started keep running (`subprocess.run` kills only the direct child) | `sh -c 'sleep 15 & sleep 15'` with a 2 s timeout leaves the inner `sleep` | Proposal P6 |

Held up under stress:

- The runs list and dashboard stay at 6 to 10 ms during uploads and with 8 readers at once.
- The CLI (`qasight ingest`) and the server writing the same SQLite file at once: no lock errors (3 CLI ingests next to 4 server uploads).
- WAL on or off made no difference at these loads.
- Runs started from the UI are capped at 2 at a time (`TESTO_MAX_CONCURRENT_RUNS`), and the engine keeps only a 64 KB tail of a stage's output in memory.

## Proposals and what was built

Taltal approved P1 to P4 on 2026-11-20.

- **P1, read the slow pages for less** (S1, S2, S8). Built: a run's failures and a test's page read only those tests' rows from the earlier runs (one primary-key query); recorded runs' rows stay in memory per process (`TESTO_CASE_CACHE_ROWS`, default 100,000 rows, about 1 KB each), dropped on replace, delete and restore; flaky tests and the cycle's test list are memoized until a run lands, and test search filters the cached list. Flaky tests still process every test of the window on the first request after a run (0.8 s at 5,000 x 20); a per-test summary kept at upload would remove that if suites grow much bigger.
- **P2, record uploads in a separate process** (S3, S6). Built: `qasight-api` and `qasight ui` set `TESTO_INGEST_PROCESS=1`, and uploads are recorded in a spawned one-worker pool, restarted after 50 uploads or when it dies. The CLI and tests record in-process.
- **P3, record a run with less work** (S4). Built: one bulk insert for the rows, stage health worked out once for both stores, redaction skips re-parsing JSON it didn't change, the report archive zips at level 1. Tried and dropped: a shared cache of parsed result files across the four readers (no gain; the OS page cache already makes re-reads cheap).
- **P4, one results file per run** (S5). Not built yet: seven features read a run's per-test files (stored cases for old runs, attachments, annotations, failure source, OpenTelemetry export, the snapshot file list, Allure reports), so this changes the storage format and needs a reader layer and a migration of existing runs. Retention (`TESTO_RETENTION_*`) bounds the disk meanwhile.
- **P5, longer client timeout for big uploads** (S6). Open: scale `UPLOAD_TIMEOUT_S` with the upload size, and answer a repeated upload (same CI job id) with the existing run.
- **P6, kill a scanner's whole process group on timeout** (S11), as the engine already does for stages. Open.

## Results after the fixes

`scripts/benchmark.py` (from PR #340, agents step skipped), 250,000 results in 50 runs of 5,000 tests, one API worker, SQLite, same container:

| Page or step | Before | After |
| --- | --- | --- |
| A run's failures, p50 | 1,984 ms | 14.5 ms |
| Flaky tests, p50 | 2,549 ms | 9.3 ms |
| Test search, p50 | 2,608 ms | 5.6 ms |
| 8 at once, each (25 runs) | 186 to 190 s | 0.1 s |
| Peak memory, 8 heavy pages at once | 970 MB | 262 MB |
| Store 250,000 results, one worker | 1,542 results/s (Benchmark guide) | 1,612 results/s |

## Decision

Fix the two clear failures (S1, S7) at once, then build the approved proposals P1 to P3 in the same pull request. P4 changes how runs are stored, so it gets its own decision.

## Consequences

- Concurrent readers on SQLite take turns on run rows; with the cache most requests don't read them at all.
- Each API process keeps up to `TESTO_CASE_CACHE_ROWS` rows in memory (about 100 MB by default), and the upload worker is a second process.
- Flaky tests and test search can be up to `TESTO_READ_CACHE_S` old for changes that are not runs; neither depends on any.
- The Benchmark guide's numbers for the three slow pages are older than these fixes; re-run `scripts/benchmark.py` for current ones.
- Not measured: Postgres on its own machine, attachments and videos in uploads, launches open for hours.
