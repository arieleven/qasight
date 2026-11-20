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
| S1 | High | A run's failures, flaky tests and test search collapse when several people open them at once on SQLite. The `sqlite3` module releases the GIL on every row, so threads reading 100,000 rows each hand the lock back and forth per row | 8 at once: 190 s each (2 s alone). Raw `sqlite3`: 8 threads 156 s, same reads in turn 1.7 s, 8 processes 0.35 s | Fixed in this PR: one SQLite read of a run's rows at a time per process, plain rows instead of ORM objects. 8 at once: 8 to 10 s each |
| S2 | High | Those three pages are still about 1 s each for one person, because each request rebuilds 20 runs x 5,000 test rows in Python | 0.73 s / 1.22 s / 1.23 s p50 after S1 (were 1.98 / 2.55 / 2.61 s) | Proposal P1 |
| S2b | High | On Postgres the same three pages cost the same as on SQLite after S1, and 8 at once take as long as 8 in turn: the time is Python building rows, not the database | 250,000 results, 4 workers: 0.70 / 1.25 / 1.31 s p50; 8 at once 7.7 / 11.2 / 10.9 s each | Proposal P1 |
| S3 | High | Uploads crawl when the server is also answering reads (one worker). An upload is CPU-bound Python plus ~300,000 small file operations per 50,000 tests, and every file operation waits for the GIL behind the readers | 8 uploads x 5,000 tests: 16 s alone, 32 s with 4 clients reading twice a second, 6 uploads in 10 min with 4 clients reading non-stop | Proposal P2, P3 |
| S4 | Medium | Recording an upload does each test several times over: JUnit becomes one Allure JSON file per test, then those files are parsed twice for metrics, twice for stage health, read again for redaction, `load_cases`, the history copy and the report zip | 50,000-test upload: 50 s. DB insert through ORM 10 s, zip 9 s, parsing 5.5 s, redaction 5.5 s, copy 4 s | Proposal P3 |
| S5 | Medium | Disk use is about ten times the uploaded size: every test is its own file in `static/history/<run>/`, plus a zip of the same files and the DB rows | 5,000-test run: 0.6 MB JUnit, 2.3 MB of JSON, 21 MB on disk (4 KB blocks). 1M results is about 1M files and 4 GB | Proposal P4 |
| S6 | Medium | One upload at a time per process (`_INGEST_LOCK`); a 100,000-test upload holds it for about a minute, and the CLI client gives up after 120 s while the server keeps recording, so a CI retry records the run twice | 100,000 tests (11 MB): 58 s, peak 770 MB RSS | Proposal P2, P5 |
| S7 | Medium | `POST /api/v1/ingest` read every file of an upload into memory before checking the 100 MB total | One 475 MB request (25 files under 20 MB): server 111 MB to 590 MB | Fixed in this PR: checked while reading |
| S8 | Medium | Memory after a burst stays high: Python keeps freed memory | 8 heavy page loads at once: peak 970 MB, 590 MB after. Steady ingests don't grow it (156 MB after 10 and after 20) | Watch; P1 shrinks the burst |
| S9 | Medium | With several workers (`TESTO_WORKERS`), a scorecard scan lived in one worker's memory: polls hit other workers and got 404, and each worker allowed its own scan | 12 of 20 polls 404; 3 scans ran at once | Fixed in PR #340 (scan state in the DB) |
| S10 | Low | The ReportPortal endpoint read the whole request body before checking its 32 MB limit | (same class as S7) | Fixed in PR #340 |
| S11 | Low | A scanner that times out is killed, but processes it started keep running (`subprocess.run` kills only the direct child) | `sh -c 'sleep 15 & sleep 15'` with a 2 s timeout leaves the inner `sleep` | Proposal P6 |

Held up under stress:

- The runs list and dashboard stay at 6 to 10 ms during uploads and with 8 readers at once.
- The CLI (`qasight ingest`) and the server writing the same SQLite file at once: no lock errors (3 CLI ingests next to 4 server uploads).
- WAL on or off made no difference at these loads.
- Runs started from the UI are capped at 2 at a time (`TESTO_MAX_CONCURRENT_RUNS`), and the engine keeps only a 64 KB tail of a stage's output in memory.

## Proposals

- **P1, read the three slow pages from SQL** (S2, S8). Count failures and flips per test in the database, with an index on (cycle, test key), and load only the run's failing rows and their history. Or keep a per-test summary row updated at upload. Either makes the pages independent of run size. A cache of finished runs' rows is the smaller step, at the cost of memory.
- **P2, record uploads in a separate process** (S3, S6). A small pool (one process to start) that the API hands the files to, so uploads don't compete with readers for the GIL, and a slow upload doesn't block the API's threads. The ingest lock becomes a queue that can answer "queued, run id X" at once.
- **P3, parse once** (S4). Keep the parsed cases from the JUnit import and pass them to metrics, health, redaction and the DB writer instead of each re-reading the files; insert rows with one Core `insert()` instead of ORM objects (10 s to about 1 s for 50,000 rows); build the report zip on first download instead of on every upload.
- **P4, one results file per run** (S5). Store a run's Allure results as one compressed file (or only in the DB) and write the per-test files only when an Allure report is generated.
- **P5, longer client timeout for big uploads** (S6). Scale `UPLOAD_TIMEOUT_S` with the upload size, and have the server answer an upload it already recorded (same CI job id) with the existing run instead of a second one.
- **P6, kill a scanner's whole process group on timeout** (S11), as the engine already does for stages.

## Decision

Fix the two clear failures now (S1, S7) in one pull request; take P1 to P6 to Taltal as proposals, since they change how uploads and reads work.

## Consequences

- Concurrent readers on SQLite now take turns on run rows: one heavy page doesn't slow the others more than running them in turn would.
- The Benchmark guide's numbers for the three slow pages are older than this fix; re-run `scripts/benchmark.py` for current ones.
- Not measured: Postgres on its own machine, attachments and videos in uploads, launches open for hours.
