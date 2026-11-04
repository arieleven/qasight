"""Unit tests for ``qasight.cli.ui.summary_dashboard`` helpers."""

from __future__ import annotations

import uuid

from rich.text import Text

from qasight.cli.ui.summary_dashboard import (
    format_delta_ms_cell,
    human_duration_ms,
    pass_rate_percent,
    suite_duration_preferred,
)
from qasight.repository.models import ReportArchive


def test_human_duration_ms() -> None:
    assert human_duration_ms(None) == "—"
    assert human_duration_ms(500) == "500 ms"
    assert "s" in human_duration_ms(1500)


def test_pass_rate_percent() -> None:
    assert pass_rate_percent(1, 4) == 25.0
    assert pass_rate_percent(None, 4) is None
    assert pass_rate_percent(1, None) is None
    assert pass_rate_percent(1, 0) is None


def test_suite_duration_preferred_prefers_allure() -> None:
    a = ReportArchive(
        id=uuid.uuid4(),
        cycle_name="x",
        exit_code=0,
        summary_json={},
        artifact_bytes=b"",
        allure_duration_ms=5000,
        plan_duration_ms=9999,
    )
    ms, label = suite_duration_preferred(a)
    assert ms == 5000
    assert label == "sum of test times"


def test_format_delta_ms_cell() -> None:
    assert format_delta_ms_cell(None).plain == "—"
    assert format_delta_ms_cell(0).plain == "-"
    assert "red" in (format_delta_ms_cell(150).style or "")
    assert "green" in (format_delta_ms_cell(-5).style or "")
    assert "yellow" in (format_delta_ms_cell(50).style or "")


def test_format_delta_ms_cell_returns_text() -> None:
    assert isinstance(format_delta_ms_cell(-1), Text)
