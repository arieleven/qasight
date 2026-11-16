"""Benchmark a QASight team server: store a million test results, then time what people do.

Reproducible on any machine::

    python scripts/benchmark.py                       # 1,000,000 results, SQLite, 1 worker
    python scripts/benchmark.py --workers 4           # four API worker processes
    python scripts/benchmark.py --database-url postgresql+psycopg://u:p@localhost/qasight
    python scripts/benchmark.py --results 100000      # a quicker run

It starts ``qasight-api`` on a free port with a fresh database (unless
``--database-url`` names one), then:

1. **Fill**: uploads ``--results`` test results as JUnit XML through ``POST /api/v1/ingest``
   (``--runs`` runs of one cycle, a few failing and flaky tests in each), ``--clients`` at a time.
2. **Agents**: ``--launches`` ReportPortal launches of ``--launch-tests`` tests each, reported
   at the same time through the ReportPortal-compatible endpoint, one call per test start and
   finish, the way agents report.
3. **Reads**: times the pages people open (runs list, dashboard, a run's failures, flaky
   tests, a test's history) ``--samples`` times each and reports p50 and p95.

Prints a Markdown table and writes the numbers to ``--out`` (JSON). The published results
are in ``docs/Processes & Guides/Benchmark.md``.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import platform
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

TOKEN = "benchmark-token"
CYCLE = "bench"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _junit(run: int, tests: int, rng: random.Random) -> bytes:
    cases: list[str] = []
    for i in range(tests):
        name = f"test_{i:06d}"
        classname = f"bench.module_{i // 100:04d}"
        # About 1 in 500 tests fails in a run; tests ending in 7 flip between runs (flaky).
        failing = rng.random() < 0.002 or (i % 1000 == 7 and run % 2 == 0)
        body = ""
        if failing:
            body = f'<failure message="AssertionError: value {rng.randint(1, 99)} != 0">trace</failure>'
        cases.append(
            f'<testcase classname="{classname}" name="{name}" time="0.01">{body}</testcase>'
        )
    xml = (
        f'<?xml version="1.0"?><testsuite name="bench" tests="{tests}">'
        + "".join(cases)
        + "</testsuite>"
    )
    return xml.encode()


def _ingest(base: str, run: int, tests: int, seed: int) -> float:
    rng = random.Random(seed + run)
    data = _junit(run, tests, rng)
    started = time.perf_counter()
    resp = requests.post(
        f"{base}/api/v1/ingest",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"cycle": CYCLE, "ci_ref_name": "main", "ci_commit_sha": f"{run:040x}"},
        files={"files": (f"run-{run}.xml", data, "application/xml")},
        timeout=600,
    )
    resp.raise_for_status()
    return time.perf_counter() - started


def _launch(base: str, index: int, tests: int) -> tuple[int, float]:
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {TOKEN}"
    api = f"{base}/api/v2/bench"
    calls = 0
    started = time.perf_counter()

    def post(path: str, body: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        resp = session.post(f"{api}/{path}", json=body, timeout=120)
        resp.raise_for_status()
        return str(resp.json()["id"])

    def put(path: str, body: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        session.put(f"{api}/{path}", json=body, timeout=600).raise_for_status()

    now = int(time.time() * 1000)
    launch = post("launch", {"name": f"agents-{index % 4}", "startTime": now})
    suite = post("item", {"launchUuid": launch, "name": "suite", "type": "SUITE", "startTime": now})
    for i in range(tests):
        item = post(
            f"item/{suite}",
            {"launchUuid": launch, "name": f"test_{i}", "type": "STEP", "startTime": now + i},
        )
        status = "FAILED" if i % 97 == 0 else "PASSED"
        put(f"item/{item}", {"launchUuid": launch, "status": status, "endTime": now + i + 1})
    put(f"item/{suite}", {"launchUuid": launch, "endTime": now + tests + 1})
    put(f"launch/{launch}/finish", {"endTime": now + tests + 2})
    return calls, time.perf_counter() - started


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


def _time_get(base: str, path: str, samples: int) -> dict[str, float]:
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {TOKEN}"
    took: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        session.get(f"{base}{path}", timeout=600).raise_for_status()
        took.append((time.perf_counter() - started) * 1000)
    return {"p50_ms": round(statistics.median(took), 1), "p95_ms": round(_percentile(took, 95), 1)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--results", type=int, default=1_000_000, help="test results to store")
    parser.add_argument("--runs", type=int, default=200, help="runs the results are spread over")
    parser.add_argument("--clients", type=int, default=4, help="uploads at the same time")
    parser.add_argument("--workers", type=int, default=1, help="API worker processes")
    parser.add_argument("--launches", type=int, default=8, help="ReportPortal launches at once")
    parser.add_argument("--launch-tests", type=int, default=250, help="tests per launch")
    parser.add_argument("--samples", type=int, default=20, help="timed requests per page")
    parser.add_argument(
        "--database-url", default=None, help="use this database (default: new SQLite)"
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default="benchmark-results.json")
    args = parser.parse_args(argv)

    tests_per_run = max(1, args.results // args.runs)
    with tempfile.TemporaryDirectory(prefix="qasight-bench-") as tmp:
        home = Path(tmp)
        port = _free_port()
        env = {
            **os.environ,
            "TESTO_HOME": str(home),
            "DATABASE_URL": args.database_url or f"sqlite:///{home / 'history.db'}",
            "TESTO_API_TOKEN": TOKEN,
            "TESTO_API_PORT": str(port),
            "TESTO_WORKERS": str(args.workers),
            "TESTO_RUN_QUEUE": "db",
        }
        server = subprocess.Popen(
            [sys.executable, "-c", "from qasight_api.main import run; raise SystemExit(run())"],
            cwd=home,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(120):
                try:
                    if requests.get(f"{base}/api/v1/health/live", timeout=2).ok:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.5)
            else:
                print("server did not start", file=sys.stderr)
                return 1

            print(
                f"fill: {tests_per_run * args.runs:,} results in {args.runs} runs ...", flush=True
            )
            started = time.perf_counter()
            with futures.ThreadPoolExecutor(args.clients) as pool:
                upload_times = list(
                    pool.map(lambda r: _ingest(base, r, tests_per_run, args.seed), range(args.runs))
                )
            fill_s = time.perf_counter() - started
            stored = tests_per_run * args.runs

            print(f"agents: {args.launches} launches x {args.launch_tests} tests ...", flush=True)
            started = time.perf_counter()
            with futures.ThreadPoolExecutor(args.launches) as pool:
                launches = list(
                    pool.map(lambda i: _launch(base, i, args.launch_tests), range(args.launches))
                )
            agents_s = time.perf_counter() - started
            calls = sum(c for c, _ in launches)

            session = requests.Session()
            session.headers["Authorization"] = f"Bearer {TOKEN}"
            runs = session.get(f"{base}/api/v1/runs?cycle={CYCLE}&limit=1", timeout=120).json()[
                "items"
            ]
            run_id = runs[0]["run_id"]
            pages = {
                "runs list": "/api/v1/runs?limit=50",
                "dashboard": "/api/v1/dashboard/overview",
                "run failures": f"/api/v1/runs/{run_id}/failures",
                "flaky tests": f"/api/v1/analytics/flaky?cycle={CYCLE}",
                "test search": f"/api/v1/analytics/tests?cycle={CYCLE}&q=test_000007",
            }
            print("reads ...", flush=True)
            reads = {name: _time_get(base, path, args.samples) for name, path in pages.items()}
        finally:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()

    result = {
        "date": time.strftime("%Y-%m-%d"),
        "machine": {
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
            "system": platform.platform(),
        },
        "settings": {
            "workers": args.workers,
            "database": "custom" if args.database_url else "sqlite",
            "clients": args.clients,
        },
        "fill": {
            "results": stored,
            "runs": args.runs,
            "seconds": round(fill_s, 1),
            "results_per_second": round(stored / fill_s),
            "upload_p50_s": round(statistics.median(upload_times), 2),
        },
        "agents": {
            "launches": args.launches,
            "tests": args.launches * args.launch_tests,
            "calls": calls,
            "seconds": round(agents_s, 1),
            "calls_per_second": round(calls / agents_s),
        },
        "reads": reads,
    }
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print()
    print("| Step | Result |\n| --- | --- |")
    print(
        f"| Store {stored:,} results ({args.runs} runs) | {fill_s:,.0f} s, {result['fill']['results_per_second']:,} results/s |"
    )
    print(
        f"| {args.launches} agents at once, {calls:,} calls | {agents_s:,.0f} s, "
        f"{result['agents']['calls_per_second']:,} calls/s |"
    )
    for name, timing in reads.items():
        print(f"| {name} | p50 {timing['p50_ms']} ms, p95 {timing['p95_ms']} ms |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
