from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunMetrics:
    timestamp: int
    total_tests: int
    passed: int
    failed: int
    broken: int
    skipped: int
    unknown: int
    duration_ms: int
    run_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "total_tests": self.total_tests,
            "passed": self.passed,
            "failed": self.failed,
            "broken": self.broken,
            "skipped": self.skipped,
            "unknown": self.unknown,
            "duration_ms": self.duration_ms,
        }


def parse_allure_results_dir(results_dir: Path) -> RunMetrics:
    """
    Parse `*-result.json` files and compute KPIs for Grafana.
    """
    results_dir = results_dir.expanduser().resolve()
    files = sorted(results_dir.rglob("*-result.json"))

    counts = {"passed": 0, "failed": 0, "broken": 0, "skipped": 0, "unknown": 0}
    total = 0
    min_start: int | None = None
    max_stop: int | None = None

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("skipping unreadable Allure result %s: %s", f, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("skipping Allure result %s: not a JSON object", f)
            continue

        status = str(data.get("status") or "unknown").lower()
        if status not in counts:
            status = "unknown"

        counts[status] += 1
        total += 1

        start = data.get("start")
        stop = data.get("stop")
        if isinstance(start, int):
            min_start = start if min_start is None else min(min_start, start)
        if isinstance(stop, int):
            max_stop = stop if max_stop is None else max(max_stop, stop)

    duration_ms = 0
    if min_start is not None and max_stop is not None and max_stop >= min_start:
        duration_ms = int(max_stop - min_start)

    ts = int(time.time())
    run_id = _read_run_id(results_dir)

    return RunMetrics(
        timestamp=ts,
        run_id=run_id,
        total_tests=total,
        passed=counts["passed"],
        failed=counts["failed"],
        broken=counts["broken"],
        skipped=counts["skipped"],
        unknown=counts["unknown"],
        duration_ms=duration_ms,
    )


def write_metrics_json(metrics: RunMetrics, *, out_path: Path) -> Path:
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return out_path


def list_run_history(
    *, archive_root: Path, current_results_dir: Path | None = None
) -> list[RunMetrics]:
    """
    Best-effort: build a history list from archived results folders + current results.

    Archive folders (written by the pre-v1.1 headless runner) look like:
      <archive_root>/<timestamp>_<run_id>/
    """
    archive_root = archive_root.expanduser().resolve()
    out: list[RunMetrics] = []

    if current_results_dir and current_results_dir.exists():
        out.append(parse_allure_results_dir(current_results_dir))

    if not archive_root.exists():
        return out

    for p in sorted([d for d in archive_root.iterdir() if d.is_dir()], reverse=True)[:50]:
        try:
            out.append(parse_allure_results_dir(p))
        except OSError:
            logger.warning("skipping unreadable archived results %s", p, exc_info=True)
            continue

    return out


def push_influxdb(
    metrics: RunMetrics,
    *,
    url: str,
    token: str,
    org: str,
    bucket: str,
    measurement: str = "testo_test_run",
) -> tuple[bool, str]:
    """
    Push a single metrics point to InfluxDB.
    """
    try:
        from influxdb_client import InfluxDBClient, Point  # type: ignore
        from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore
    except ImportError as exc:
        return False, f"influxdb-client not available: {exc}"

    try:
        client = InfluxDBClient(url=url, token=token, org=org)
        write_api = client.write_api(write_options=SYNCHRONOUS)

        p = Point(measurement).time(metrics.timestamp)
        if metrics.run_id:
            p = p.tag("run_id", metrics.run_id)

        p = (
            p.field("total_tests", int(metrics.total_tests))
            .field("passed", int(metrics.passed))
            .field("failed", int(metrics.failed))
            .field("broken", int(metrics.broken))
            .field("skipped", int(metrics.skipped))
            .field("unknown", int(metrics.unknown))
            .field("duration_ms", int(metrics.duration_ms))
        )

        write_api.write(bucket=bucket, org=org, record=p)
        client.close()
        return True, "Pushed metrics to InfluxDB."
    except Exception as exc:
        # Broad on purpose: influxdb-client raises many transport/API types; the caller gets (False, msg).
        logger.warning("InfluxDB push failed", exc_info=True)
        return False, f"Influx push failed: {exc}"


def _read_run_id(results_dir: Path) -> str | None:
    # Prefer environment.properties if present.
    env = results_dir / "environment.properties"
    if env.exists():
        try:
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("TESTO_RUN_ID="):
                    return line.split("=", 1)[1].strip() or None
        except (OSError, ValueError):
            logger.debug("could not read %s", env, exc_info=True)

    # Fallback: parse suffix from archive folder name: "<timestamp>_<runid>"
    name = results_dir.name
    if "_" in name:
        maybe = name.split("_", 1)[1]
        return maybe or None
    return None
