"""Tests for integration façade functions (mock HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import qasight.reporting.integrations as integrations


def test_test_prometheus_pushgateway_ok() -> None:
    with patch("qasight.reporting.integrations.requests.get") as get:
        get.return_value = MagicMock(status_code=200, text="ok")
        ok, _msg = integrations.test_prometheus_pushgateway(pushgateway_url="http://localhost:9091")
        assert ok is True


def test_test_prometheus_pushgateway_empty_url() -> None:
    ok, msg = integrations.test_prometheus_pushgateway(pushgateway_url=None)
    assert ok is False and "PROMETHEUS" in msg
