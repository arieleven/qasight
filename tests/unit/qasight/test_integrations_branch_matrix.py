from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import qasight.reporting.integrations as integrations
from qasight.reporting.metrics import RunMetrics
from tests.unit.cases.strings import common_string_cases

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize("case", common_string_cases(), ids=lambda c: c.name)
def test_escape_prom_label_value_never_contains_raw_newlines(case) -> None:
    out = integrations._escape_prom_label_value(case.value)
    assert "\n" not in out


@pytest.mark.parametrize(
    "metrics",
    [
        RunMetrics(
            timestamp=1,
            total_tests=0,
            passed=0,
            failed=0,
            broken=0,
            skipped=0,
            unknown=0,
            duration_ms=0,
            run_id="r",
        ),
        RunMetrics(
            timestamp=1,
            total_tests=2,
            passed=1,
            failed=1,
            broken=0,
            skipped=0,
            unknown=0,
            duration_ms=10,
            run_id="rid",
        ),
    ],
)
def test_prometheus_exposition_contains_expected_gauges(metrics: RunMetrics) -> None:
    body = integrations._prometheus_exposition(metrics)
    assert "testo_total_tests" in body
    assert "testo_passed" in body
    assert "testo_failed" in body
    assert f"{int(metrics.total_tests)}" in body


def test_push_to_prometheus_http_error_branch() -> None:
    m = RunMetrics(
        timestamp=1,
        total_tests=1,
        passed=0,
        failed=1,
        broken=0,
        skipped=0,
        unknown=0,
        duration_ms=1,
        run_id="rid",
    )
    with patch(
        "qasight.reporting.integrations.prometheus_settings_from_env",
        return_value={"pushgateway_url": "http://x", "job_name": "qasight"},
    ):
        with patch("qasight.reporting.integrations.requests.post") as post:
            post.return_value = MagicMock(status_code=500, text="boom")
            ok, msg = integrations.push_to_prometheus(m)
    assert ok is False
    assert "HTTP 500" in msg


def test_push_to_prometheus_missing_url_branch() -> None:
    m = RunMetrics(
        timestamp=1,
        total_tests=1,
        passed=1,
        failed=0,
        broken=0,
        skipped=0,
        unknown=0,
        duration_ms=1,
        run_id="rid",
    )
    with patch(
        "qasight.reporting.integrations.prometheus_settings_from_env",
        return_value={"pushgateway_url": None, "job_name": "qasight"},
    ):
        ok, msg = integrations.push_to_prometheus(m)
    assert ok is False
    assert "PROMETHEUS_PUSHGATEWAY_URL" in msg


@pytest.mark.parametrize(
    "env_value,expected",
    [
        (None, "d"),
        ("", "d"),
        ("   ", "d"),
        ("x", "x"),
        ("  x  ", "x"),
    ],
)
def test__env_normalization(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: str | None
) -> None:
    name = "TESTO_TEST_ENV_HELPER"
    monkeypatch.delenv(name, raising=False)
    if env_value is not None:
        monkeypatch.setenv(name, env_value)
    assert integrations._env(name, "d") == expected
