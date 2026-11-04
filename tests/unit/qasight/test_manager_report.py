"""The manager report (qasight/services/manager_report.py) and ``GET /api/v1/report``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from qasight.history.views import CompletedRunView
from qasight.services import manager_report
from tests.unit.qasight.test_run_digest import DAY, NOW, history  # noqa: F401  (fixture)


@pytest.fixture
def report_history(
    history: list[CompletedRunView],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> list[CompletedRunView]:
    monkeypatch.setattr(manager_report, "list_recent_runs", lambda *, limit: history)
    health = SimpleNamespace(
        score=81,
        by_cycle=[SimpleNamespace(cycle="api", score=81)],
        findings=[SimpleNamespace(cycle="api", title="api is red", detail="1 test failing.")],
    )
    monkeypatch.setattr(manager_report, "project_health", lambda cycle=None: health)  # noqa: ARG005
    return history


def test_weekly_report_numbers_trend_and_lists(report_history: list[CompletedRunView]) -> None:
    report = manager_report.build_report(period="week", now=NOW)
    k = report.kpis
    assert (k.runs, k.green, k.prev_runs, k.prev_green) == (3, 1, 2, 0)
    assert (k.health_score, k.health_grade, k.cycles) == (81, "B", 1)
    assert (k.failing_tests, k.newly_flaky, k.fixed) == (1, 1, 1)
    assert k.pass_rate == pytest.approx(77.8) and k.prev_pass_rate == pytest.approx(66.7)
    assert report.title == "Weekly test report"
    assert report.summary[0] == "3 runs across 1 cycle; 1 green (33%)."
    assert report.summary[1] == "The test pass rate rose from 66.7% to 77.8%."
    assert "1 test still failing, 1 test turned flaky, 1 test fixed." in report.summary
    assert report.summary[-1] == "Test health score: 81/100 (grade B)."

    assert len(report.trend) == 7
    assert sum(p.runs for p in report.trend) == 3
    busy = [p for p in report.trend if p.runs]
    assert [(p.runs, p.green) for p in busy] == [(1, 0), (1, 1), (1, 0)]

    (row,) = report.cycles
    assert (row.cycle, row.runs, row.failing, row.health_score) == ("api", 3, 1, 81)
    assert [t.name for t in report.still_failing] == ["test_flip"]
    assert [t.name for t in report.fixed] == ["test_login"]
    assert report.slower[0].detail == "0.10s → 0.90s"
    assert report.findings[0].title == "api is red"
    assert report.to_dict()["kpis"]["runs"] == 3

    # Delivery: test_login failed 13 days ago and passed 6 days ago; test_flip is flaky.
    d = report.delivery
    assert (d.fixes, d.median_fix_s, d.open_failures) == (1, 7 * DAY, 0)
    assert d.ci_minutes == pytest.approx(0.1)  # three one-second runs, rounded
    assert "Failing tests were fixed in a median of 7.0d, over 1 fix." in report.summary
    assert busy[0].median_duration_ms == 1000


def test_empty_period_and_bad_period(report_history: list[CompletedRunView]) -> None:
    report = manager_report.build_report(period="day", now=NOW - 2 * DAY)
    assert report.kpis.runs == 0
    assert report.summary == ["No runs were recorded this day."]
    assert report.cycles == []
    with pytest.raises(ValueError):
        manager_report.build_report(period="year")


def test_report_api(
    report_history: list[CompletedRunView], monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from qasight_api.main import create_app

    real = manager_report.build_report
    monkeypatch.setattr(
        "qasight_api.routes.report.build_report",
        lambda **kw: real(**kw, now=NOW),
    )
    client = TestClient(create_app())
    body = client.get("/api/v1/report?period=month&cycle=api&title=Q4%20check-in").json()
    assert body["title"] == "Q4 check-in"
    assert body["period"] == "month" and body["days"] == 30 and body["scope"] == ["api"]
    assert len(body["trend"]) == 30
    assert client.get("/api/v1/report?period=year").status_code == 422
