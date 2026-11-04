"""More coverage for ``qasight.reporting.integrations`` (all mocked network / extractors)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from qasight.reporting.metrics import RunMetrics


@pytest.fixture
def sample_metrics() -> RunMetrics:
    return RunMetrics(
        timestamp=1,
        total_tests=2,
        passed=2,
        failed=0,
        broken=0,
        skipped=0,
        unknown=0,
        duration_ms=10,
        run_id="rid",
    )


def test_integration_status_from_env_false(monkeypatch: pytest.MonkeyPatch) -> None:
    from qasight.reporting.integrations import integration_status_from_env

    monkeypatch.delenv("INFLUXDB_URL", raising=False)
    monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)
    monkeypatch.delenv("INFLUXDB_ORG", raising=False)
    monkeypatch.delenv("INFLUXDB_BUCKET", raising=False)
    monkeypatch.delenv("PROMETHEUS_PUSHGATEWAY_URL", raising=False)
    st = integration_status_from_env()
    assert st["influx_configured"] is False
    assert st["prometheus_configured"] is False


def test_push_to_prometheus_missing_url(sample_metrics: RunMetrics) -> None:
    from qasight.reporting.integrations import push_to_prometheus

    ok, msg = push_to_prometheus(sample_metrics, pushgateway_url=None)
    assert ok is False
    assert "PROMETHEUS_PUSHGATEWAY_URL" in msg


def test_push_to_prometheus_http_error(sample_metrics: RunMetrics) -> None:
    from qasight.reporting.integrations import push_to_prometheus

    with patch("qasight.reporting.integrations.requests.post") as post:
        post.return_value = MagicMock(status_code=400, text="bad")
        ok, msg = push_to_prometheus(
            sample_metrics, pushgateway_url="http://x:9091", job_name="qasight"
        )
    assert ok is False
    assert "HTTP 400" in msg


def _write_result(results_root: Path, status: str) -> None:
    stage_results = results_root / "unit" / "allure-results" / "pytest"
    stage_results.mkdir(parents=True, exist_ok=True)
    (stage_results / f"{status}-result.json").write_text(
        json.dumps({"status": status, "start": 0, "stop": 10}), encoding="utf-8"
    )


def test_push_run_metrics_does_nothing_when_no_target_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.reporting.integrations import push_run_metrics_if_configured

    for name in (
        "INFLUXDB_URL",
        "INFLUXDB_TOKEN",
        "INFLUXDB_ORG",
        "INFLUXDB_BUCKET",
        "PROMETHEUS_PUSHGATEWAY_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    _write_result(tmp_path, "passed")
    assert push_run_metrics_if_configured(results_root=tmp_path, run_id="rid") == []


def test_push_run_metrics_reports_missing_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.reporting.integrations import push_run_metrics_if_configured

    monkeypatch.setenv("PROMETHEUS_PUSHGATEWAY_URL", "http://x:9091")
    out = push_run_metrics_if_configured(results_root=tmp_path, run_id="rid")
    assert out and out[0][0] == "metrics" and out[0][1] is False


def test_push_run_metrics_pushes_to_each_configured_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.reporting.integrations import push_run_metrics_if_configured

    for name, value in {
        "INFLUXDB_URL": "http://influx:8086",
        "INFLUXDB_TOKEN": "t",
        "INFLUXDB_ORG": "o",
        "INFLUXDB_BUCKET": "b",
        "PROMETHEUS_PUSHGATEWAY_URL": "http://x:9091",
    }.items():
        monkeypatch.setenv(name, value)
    _write_result(tmp_path, "passed")
    _write_result(tmp_path, "failed")
    pushed: list[RunMetrics] = []

    def fake_push(metrics: RunMetrics, **_kwargs):  # noqa: ANN003
        pushed.append(metrics)
        return True, "ok"

    with patch("qasight.reporting.integrations.push_to_influxdb", side_effect=fake_push):
        with patch("qasight.reporting.integrations.push_to_prometheus", side_effect=fake_push):
            out = push_run_metrics_if_configured(results_root=tmp_path, run_id="rid")

    assert [t for t, _ok, _msg in out] == ["influxdb", "prometheus"]
    assert pushed[0].total_tests == 2 and pushed[0].failed == 1
    assert pushed[0].run_id == "rid"
