---
type: guide
status: current
created: 2026-11-16
updated: 2026-11-16
---

# Benchmark

[Index](../Index.md) · [Team Server](Team%20Server.md)

How much one QASight team server holds, measured with `scripts/benchmark.py`, so a team can check the numbers on its own hardware instead of taking ours.

## Run it

```bash
python scripts/benchmark.py                        # 1,000,000 results, SQLite, 1 worker
python scripts/benchmark.py --workers 4            # four API worker processes
python scripts/benchmark.py --database-url postgresql+psycopg://u:p@localhost/qasight
python scripts/benchmark.py --results 100000       # a quicker run
```

It starts `qasight-api` on a free port with a new database, then:

1. **Fill:** uploads the results as JUnit XML through `POST /api/v1/ingest`, 200 runs of one cycle with 5,000 tests each, four uploads at a time. About 1 in 500 tests fails in a run, and some flip between runs (flaky).
2. **Agents:** eight ReportPortal launches of 250 tests each report at the same time through the [ReportPortal-compatible endpoint](Moving%20from%20ReportPortal.md), one call per test start and finish, the way agents do.
3. **Reads:** opens the runs list, the dashboard, a run's failures, flaky tests and a test search 20 times each, and reports the median (p50) and p95.

It prints a Markdown table and writes the numbers to `--out` (JSON).

## Results, 2026-11-16

A 4-CPU Linux container, Python 3.13, SQLite (write-ahead mode), the 1.6 code plus [Surpass ReportPortal - 2026-11-16](../Specs%20&%20ADRs/Surpass%20ReportPortal%20-%202026-11-16.md).

| Step | 4 workers | 1 worker |
| --- | --- | --- |
| Store 1,000,000 results (200 runs) | 372 s, 2,685 results/s | 648 s, 1,542 results/s |
| 8 agents at once, 4,032 calls | 19 s, 209 calls/s | 39 s, 103 calls/s |
| Runs list | p50 9.6 ms, p95 10.0 ms | p50 7.1 ms, p95 11.3 ms |
| Dashboard | p50 9.8 ms, p95 15.9 ms | p50 6.9 ms, p95 7.2 ms |
| A run's failures | p50 2.5 s, p95 2.7 s | p50 2.1 s, p95 2.6 s |
| Flaky tests | p50 2.9 s, p95 3.3 s | p50 2.5 s, p95 2.7 s |
| Test search | p50 2.8 s, p95 3.4 s | p50 2.6 s, p95 2.8 s |

What it means:

- One machine stores a million results in about six minutes, and keeps taking agents' calls while it does; a team uploading a few thousand results a minute is far below that.
- Four workers store results 1.7 times as fast as one and take agents' calls twice as fast; reads don't get faster, since one page is one request.
- The runs list and dashboard stay near 10 ms with a million results.
- The pages that read a cycle's recent results (a run's failures, flaky tests, test search) take 2 to 3 seconds at 5,000 tests per run, because they read the stored result files of the last runs. They are the next thing to speed up.
- The machine has four CPUs and the benchmark's own clients share them, so more workers help less here than on a server of its own. Postgres on its own machine takes the database work off the API's CPUs.
