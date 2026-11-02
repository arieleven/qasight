"""Issues with red tests (2026-11-02): a cycle's tests grouped by the issue they point at."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from testo_api.main import create_app
from testo_core.services import test_issues
from testo_core.services.test_history import CycleTests, TestSummary

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _test(key: str, status: str, *, links=(), labels=(), flaky: bool = False) -> TestSummary:  # noqa: ANN001
    return TestSummary(
        key=key,
        name=key,
        full_name=key,
        group="g",
        stage="s",
        runs=3,
        passed=2,
        failed=1,
        last_status=status,
        flaky=flaky,
        last_duration_ms=None,
        median_duration_ms=None,
        slowdown_ms=None,
        slowdown_pct=None,
        labels=tuple(labels),
        links=tuple(links),
    )


JIRA = {"name": "PAY-7", "url": "https://jira.example/browse/PAY-7", "type": "issue"}


@pytest.fixture
def tests(monkeypatch: pytest.MonkeyPatch) -> None:
    found = (
        _test("charge", "failed", links=[JIRA]),
        _test("refund", "passed", links=[JIRA], labels=[{"name": "issue", "value": "pay-7"}]),
        _test("login", "passed", labels=[{"name": "issue", "value": "AUTH-1"}], flaky=True),
        _test(
            "docs",
            "failed",
            links=[{"name": "guide", "url": "https://docs.example", "type": "link"}],
        ),
        _test(
            "cart",
            "passed",
            links=[{"name": "", "url": "https://github.com/a/b/issues/3", "type": "tms"}],
        ),
    )
    monkeypatch.setattr(
        test_issues,
        "list_cycle_tests",
        lambda cycle, window: CycleTests(cycle=cycle, runs_considered=4, tests=found),
    )


@pytest.mark.usefixtures("tests")
def test_issues_group_links_and_labels_red_first() -> None:
    found = test_issues.cycle_issues("api")
    assert (found.linked_tests, found.total_tests, found.runs_considered) == (4, 5, 4)
    summary = [(i.issue, i.url, i.tests, i.failing, i.state) for i in found.issues]
    assert summary == [
        ("PAY-7", "https://jira.example/browse/PAY-7", 2, 1, "failing"),
        ("AUTH-1", None, 1, 0, "flaky"),
        ("https://github.com/a/b/issues/3", "https://github.com/a/b/issues/3", 1, 0, "passing"),
    ]
    assert [t.key for t in found.issues[0].examples] == ["charge", "refund"]


@pytest.mark.usefixtures("tests")
def test_issues_route() -> None:
    body = TestClient(create_app()).get("/api/v1/analytics/issues?cycle=api").json()
    assert body["items"][0]["issue"] == "PAY-7"
    assert body["items"][0]["state"] == "failing"
    assert body["items"][0]["examples"][0] == {
        "key": "charge",
        "name": "charge",
        "last_status": "failed",
        "flaky": False,
    }
