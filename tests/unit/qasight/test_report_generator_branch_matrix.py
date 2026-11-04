from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from qasight.reporting.report_generator import compute_system_health_pct, generate_allure_html

pytestmark = [pytest.mark.unit]


def test_compute_system_health_pct_none_when_dir_missing(tmp_path: Path) -> None:
    assert compute_system_health_pct(tmp_path / "missing") is None


def test_compute_system_health_pct_none_when_total_zero(tmp_path: Path) -> None:
    res = tmp_path / "allure-results"
    res.mkdir()
    assert compute_system_health_pct(res) is None


def test_generate_allure_html_error_branch_when_cli_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = tmp_path / "allure-results"
    res.mkdir()
    out = tmp_path / "out"

    p = MagicMock()
    p.returncode = 2
    p.stderr = "bad"
    p.stdout = ""

    def fake_run(_cmd: list[str], **_kw: object):
        return p

    ok, msg, health = generate_allure_html(results_dir=res, report_dir=out, subprocess_run=fake_run)
    assert ok is False
    assert "exit 2" in msg
    assert health is None
