"""Push run KPIs to InfluxDB and a Prometheus Pushgateway, configured via environment.

:func:`push_run_metrics_if_configured` is the post-run hook
(:class:`qasight.services.cycle_run.CycleRunService`); the rest are the
per-target clients and connection checks.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .metrics import RunMetrics, parse_allure_results_dir
from .metrics import push_influxdb as _push_influx_core

logger = logging.getLogger(__name__)


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is not None and str(v).strip() != "":
        return str(v).strip()
    return default


def influx_settings_from_env() -> dict[str, str | None]:
    return {
        "url": _env("INFLUXDB_URL"),
        "token": _env("INFLUXDB_TOKEN"),
        "org": _env("INFLUXDB_ORG"),
        "bucket": _env("INFLUXDB_BUCKET"),
    }


def prometheus_settings_from_env() -> dict[str, str | None]:
    return {
        "pushgateway_url": _env("PROMETHEUS_PUSHGATEWAY_URL"),
        "job_name": _env("PROMETHEUS_JOB_NAME", "qasight"),
    }


def push_to_influxdb(
    metrics: RunMetrics,
    *,
    url: str | None = None,
    token: str | None = None,
    org: str | None = None,
    bucket: str | None = None,
    measurement: str = "testo_test_run",
) -> tuple[bool, str]:
    """
    Push metrics to InfluxDB. When arguments are omitted, reads ``INFLUXDB_*`` from the environment
    (typically loaded from ``.env``).
    """
    try:
        s = influx_settings_from_env()
        u = url or s["url"]
        t = token or s["token"]
        o = org or s["org"]
        b = bucket or s["bucket"]
        if not u or not t or not o or not b:
            return (
                False,
                "InfluxDB: set INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG, and INFLUXDB_BUCKET (e.g. in .env).",
            )
        return _push_influx_core(metrics, url=u, token=t, org=o, bucket=b, measurement=measurement)
    except Exception as exc:
        # Broad on purpose: the contract is (ok, message), never an exception.
        logger.warning("InfluxDB push error", exc_info=True)
        return False, f"InfluxDB push error: {exc}"


def _escape_prom_label_value(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _prometheus_exposition(metrics: RunMetrics) -> str:
    """OpenMetrics-style text for Pushgateway."""
    rid = _escape_prom_label_value(metrics.run_id or "unknown")
    lines = [
        "# HELP testo_total_tests Total test cases in Allure aggregate",
        "# TYPE testo_total_tests gauge",
        f'testo_total_tests{{run_id="{rid}"}} {int(metrics.total_tests)}',
        "# HELP testo_passed Passed tests",
        "# TYPE testo_passed gauge",
        f'testo_passed{{run_id="{rid}"}} {int(metrics.passed)}',
        "# HELP testo_failed Failed tests",
        "# TYPE testo_failed gauge",
        f'testo_failed{{run_id="{rid}"}} {int(metrics.failed)}',
        "# HELP testo_duration_ms Aggregate span (ms)",
        "# TYPE testo_duration_ms gauge",
        f'testo_duration_ms{{run_id="{rid}"}} {int(metrics.duration_ms)}',
    ]
    return "\n".join(lines) + "\n"


def push_to_prometheus(
    metrics: RunMetrics,
    *,
    pushgateway_url: str | None = None,
    job_name: str | None = None,
) -> tuple[bool, str]:
    """
    Push metrics to a Prometheus Pushgateway (``POST /metrics/job/<job>``).
    Uses ``PROMETHEUS_PUSHGATEWAY_URL`` and ``PROMETHEUS_JOB_NAME`` from the environment when omitted.
    """
    try:
        s = prometheus_settings_from_env()
        base = (pushgateway_url or s["pushgateway_url"] or "").rstrip("/")
        job = job_name or s["job_name"] or "qasight"
        if not base:
            return False, "Prometheus: set PROMETHEUS_PUSHGATEWAY_URL (e.g. http://localhost:9091)."
        url = f"{base}/metrics/job/{quote(job, safe='')}"
        body = _prometheus_exposition(metrics)
        r = requests.post(
            url,
            data=body.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
            timeout=15,
        )
        if r.status_code >= 400:
            return False, f"Pushgateway HTTP {r.status_code}: {(r.text or '')[:500]}"
        return True, "Pushed metrics to Prometheus Pushgateway."
    except Exception as exc:
        # Broad on purpose: the contract is (ok, message), never an exception.
        logger.warning("Prometheus push error", exc_info=True)
        return False, f"Prometheus push error: {exc}"


def test_influxdb_connection(
    *,
    url: str | None = None,
    token: str | None = None,
    org: str | None = None,
) -> tuple[bool, str]:
    try:
        s = influx_settings_from_env()
        u = url or s["url"]
        t = token or s["token"]
        o = org or s["org"]
        if not u or not t or not o:
            return False, "Missing INFLUXDB_URL, INFLUXDB_TOKEN, or INFLUXDB_ORG."
        from influxdb_client import InfluxDBClient  # type: ignore

        client = InfluxDBClient(url=u, token=t, org=o, timeout=10_000)
        try:
            ping = getattr(client, "ping", None)
            if callable(ping):
                try:
                    ping()
                except Exception:
                    # Broad on purpose: older servers reject /ping in client-specific ways; try /health.
                    logger.debug("InfluxDB ping failed, falling back to /health", exc_info=True)
                    h = client.health()
                    st = getattr(h, "status", "")
                    if st and str(st).lower() != "pass":
                        return False, f"InfluxDB health: {h}"
            else:
                h = client.health()
                st = getattr(h, "status", "")
                if st and str(st).lower() != "pass":
                    return False, f"InfluxDB health: {h}"
        finally:
            client.close()
        return True, "InfluxDB: connection OK."
    except Exception as exc:
        # Broad on purpose: a connection check reports any failure as (False, message).
        logger.debug("InfluxDB connection test failed", exc_info=True)
        return False, f"InfluxDB test failed: {exc}"


def test_prometheus_pushgateway(*, pushgateway_url: str | None = None) -> tuple[bool, str]:
    s = prometheus_settings_from_env()
    base = (pushgateway_url or s["pushgateway_url"] or "").rstrip("/")
    if not base:
        return False, "Prometheus: set PROMETHEUS_PUSHGATEWAY_URL."
    for path in ("/-/healthy", "/metrics", "/"):
        try:
            r = requests.get(f"{base}{path}", timeout=10)
            if r.status_code < 500:
                return True, "Pushgateway: reachable."
        except requests.RequestException:
            logger.debug("Pushgateway probe %s%s failed", base, path, exc_info=True)
            continue
    return False, "Pushgateway: could not reach endpoint."


def push_run_metrics_if_configured(
    *, results_root: Path, run_id: str | None
) -> list[tuple[str, bool, str]]:
    """Push a finished run's test KPIs to every metrics target configured in the environment.

    Targets are opt-in by configuration: InfluxDB when all ``INFLUXDB_*`` settings are
    set, Prometheus when ``PROMETHEUS_PUSHGATEWAY_URL`` is set. KPIs come from every
    Allure ``*-result.json`` under *results_root* (a cycle's artifacts dir).
    Never raises; returns ``(target, ok, message)`` per attempted push, or ``[]``
    when nothing is configured.
    """
    status = integration_status_from_env()
    if not status["influx_configured"] and not status["prometheus_configured"]:
        return []
    out: list[tuple[str, bool, str]] = []
    try:
        metrics = parse_allure_results_dir(results_root)
        if metrics.total_tests == 0:
            return [("metrics", False, f"No Allure results under {results_root}.")]
        metrics = replace(metrics, run_id=run_id)
        if status["influx_configured"]:
            out.append(("influxdb", *push_to_influxdb(metrics)))
        if status["prometheus_configured"]:
            out.append(("prometheus", *push_to_prometheus(metrics)))
    except Exception as exc:
        # Broad on purpose: a post-run hook that is documented never to raise.
        logger.warning("metrics push for %s failed", results_root, exc_info=True)
        out.append(("metrics", False, str(exc)))
    return out


def integration_status_from_env() -> dict[str, Any]:
    """Lightweight readiness flags for UI (no network by default)."""
    inf = influx_settings_from_env()
    pr = prometheus_settings_from_env()
    return {
        "influx_configured": bool(inf["url"] and inf["token"] and inf["org"] and inf["bucket"]),
        "prometheus_configured": bool(pr["pushgateway_url"]),
    }
