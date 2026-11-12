"""Release gate load test: people reading the dashboard while CI uploads results.

    locust -f scripts/release_gate/locustfile.py --headless -u 50 -r 10 -t 60s \
        -H http://127.0.0.1:8765

Fails (exit code 1) when any request fails or the 95th percentile over all
requests is above ``GATE_P95_MS`` (default 2500 ms). One simulated CI job
uploads a 300-test JUnit report every 5-10 seconds; everyone else browses.
"""

from __future__ import annotations

import os
import random
import uuid

from locust import HttpUser, between, events, task


def _junit(cases: int = 300) -> bytes:
    rows = []
    for i in range(cases):
        failure = '<failure message="boom">assert 1 == 2</failure>' if i % 37 == 0 else ""
        rows.append(
            f'<testcase classname="load.suite{i % 12}" name="test_{i}" time="0.{i % 9}">{failure}</testcase>'
        )
    return f'<testsuite name="load" tests="{cases}">{"".join(rows)}</testsuite>'.encode()


JUNIT = _junit()
P95_MS = float(os.environ.get("GATE_P95_MS", "2500"))


class Viewer(HttpUser):
    """Someone with the dashboard, run pages and reports open."""

    wait_time = between(0.5, 2)

    def on_start(self) -> None:
        body = self.client.get("/api/v1/runs?limit=30").json()
        items = body.get("runs") or body.get("items") or []
        self.run_ids = [r["run_id"] for r in items] or ["none"]

    @task(5)
    def dashboard(self) -> None:
        self.client.get("/api/v1/dashboard/overview?recent_limit=6")
        self.client.get("/api/v1/dashboard/attention")

    @task(3)
    def runs(self) -> None:
        self.client.get("/api/v1/runs?limit=30")

    @task(3)
    def run_detail(self) -> None:
        self.client.get(f"/api/v1/runs/{random.choice(self.run_ids)}", name="/api/v1/runs/{id}")

    @task(2)
    def cycles(self) -> None:
        self.client.get("/api/v1/cycles")

    @task(2)
    def flaky(self) -> None:
        self.client.get("/api/v1/analytics/flaky?cycle=load-test", name="/api/v1/analytics/flaky")

    @task(1)
    def health(self) -> None:
        self.client.get("/api/v1/project-health")

    @task(1)
    def report(self) -> None:
        self.client.get("/api/v1/report")


class CiUploader(HttpUser):
    """One CI job uploading a 300-test JUnit report now and then."""

    wait_time = between(5, 10)
    fixed_count = 1

    @task
    def ingest(self) -> None:
        self.client.post(
            "/api/v1/ingest",
            files={"files": ("junit.xml", JUNIT, "application/xml")},
            data={"cycle": "load-test", "ci_commit_sha": uuid.uuid4().hex[:12]},
            name="/api/v1/ingest",
        )


@events.quitting.add_listener
def _gate(environment, **_kwargs) -> None:  # noqa: ANN001
    total = environment.stats.total
    p95 = total.get_response_time_percentile(0.95)
    print(
        f"load gate: {total.num_requests} requests, {total.num_failures} failed, p95 {p95:.0f} ms (limit {P95_MS:.0f})"
    )
    if total.num_failures or p95 > P95_MS:
        environment.process_exit_code = 1
