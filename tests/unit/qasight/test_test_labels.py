"""Test labels and links (2026-11-02): what a test is about, read from its results.

Labels come from Allure labels (feature, story, severity, owner, tag...),
Playwright tags and annotations, and a JUnit test case's own ``<properties>``.
They ride on the case map, the per-test summary and the run's failing tests,
and ``cycle_labels`` counts a cycle's tests by label.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.reporting.allure_cases import case_labels, case_links, load_cases
from qasight.reporting.junit_import import import_junit_reports
from qasight.reporting.playwright_import import _annotation_links
from qasight.services import test_history, test_labels
from qasight.services.test_history import TestSummary, has_label

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def test_structural_allure_labels_are_left_out_and_duplicates_dropped() -> None:
    data = {
        "labels": [
            {"name": "package", "value": "tests.api"},
            {"name": "suite", "value": "Api"},
            {"name": "framework", "value": "pytest"},
            {"name": "feature", "value": "Checkout"},
            {"name": "Severity", "value": "critical"},
            {"name": "tag", "value": "smoke"},
            {"name": "tag", "value": "smoke"},
            {"name": "owner", "value": ""},
        ]
    }
    assert case_labels(data) == [
        {"name": "feature", "value": "Checkout"},
        {"name": "severity", "value": "critical"},
        {"name": "tag", "value": "smoke"},
    ]


def test_only_http_links_are_kept() -> None:
    data = {
        "links": [
            {"name": "PROJ-1", "url": "https://jira.example/browse/PROJ-1", "type": "issue"},
            {"name": "evil", "url": "javascript:alert(1)"},
            {"url": "https://docs.example/page"},
        ]
    }
    assert case_links(data) == [
        {"name": "PROJ-1", "url": "https://jira.example/browse/PROJ-1", "type": "issue"},
        {"name": "https://docs.example/page", "url": "https://docs.example/page", "type": "link"},
    ]


def test_labels_are_bounded() -> None:
    data = {"labels": [{"name": "tag", "value": f"t{i}"} for i in range(50)]}
    assert len(case_labels(data)) == 20


def test_junit_testcase_properties_become_labels_and_links(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "report.xml").write_text(
        """<testsuite name="api" tests="1">
          <testcase classname="tests.test_pay" name="test_card" time="0.1">
            <properties>
              <property name="feature" value="Checkout"/>
              <property name="tags" value="smoke, payments"/>
              <property name="issue" value="https://jira.example/browse/PAY-7"/>
              <property name="jira" value="PAY-8"/>
              <property name="attachment" value="screen.png"/>
            </properties>
          </testcase>
        </testsuite>""",
        encoding="utf-8",
    )
    results_dir = tmp_path / "results" / "api" / "allure-results"
    import_junit_reports(
        target_repo=repo, patterns=("report.xml",), results_dir=results_dir, tool="pytest"
    )
    (case,) = load_cases(tmp_path / "results").values()

    assert case["labels"] == [
        {"name": "feature", "value": "Checkout"},
        {"name": "tag", "value": "smoke"},
        {"name": "tag", "value": "payments"},
        {"name": "issue", "value": "PAY-8"},
    ]
    assert case["links"] == [
        {"name": "PAY-7", "url": "https://jira.example/browse/PAY-7", "type": "issue"}
    ]


def test_playwright_issue_annotations_become_links() -> None:
    links = _annotation_links(
        [
            {"type": "issue", "description": "https://github.com/o/r/issues/12"},
            {"type": "skip", "description": "not on CI"},
            {"type": "docs", "description": "https://docs.example/login"},
        ]
    )
    assert links == [
        {"name": "12", "url": "https://github.com/o/r/issues/12", "type": "issue"},
        {"name": "login", "url": "https://docs.example/login", "type": "link"},
    ]


def test_has_label_matches_name_and_value_or_a_bare_value() -> None:
    labels = ({"name": "feature", "value": "Checkout"}, {"name": "tag", "value": "smoke"})
    assert has_label(labels, "feature:checkout")
    assert has_label(labels, "Smoke")
    assert not has_label(labels, "story:Checkout")
    assert not has_label((), "smoke")


def _summary(key: str, status: str, labels: list[dict[str, str]], flaky: bool = False):  # noqa: ANN202
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
    )


def test_cycle_labels_counts_tests_failing_and_flaky_features_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = {"name": "feature", "value": "Checkout"}
    search = {"name": "feature", "value": "Search"}
    smoke = {"name": "tag", "value": "smoke"}
    tests = (
        _summary("a", "failed", [checkout, smoke]),
        _summary("b", "passed", [checkout], flaky=True),
        _summary("c", "passed", [search, smoke]),
        _summary("d", "passed", []),
    )
    monkeypatch.setattr(
        test_labels,
        "list_cycle_tests",
        lambda cycle, window: test_history.CycleTests(cycle=cycle, runs_considered=4, tests=tests),
    )

    found = test_labels.cycle_labels("unit")

    assert (found.labelled_tests, found.total_tests, found.runs_considered) == (3, 4, 4)
    assert [(c.filter, c.tests, c.failing, c.flaky) for c in found.labels] == [
        ("feature:Checkout", 2, 1, 1),
        ("feature:Search", 1, 0, 0),
        ("tag:smoke", 2, 1, 0),
    ]


def test_list_cycle_tests_filters_by_label(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from qasight import paths
    from qasight.services import flaky_service
    from tests.unit.qasight.test_test_history import _run

    run = _run("r1", created_at=1.0)
    folder = tmp_path / str(run.snapshot_dir) / "api" / "allure-results"
    folder.mkdir(parents=True)
    for name, feature in (("test_pay", "Checkout"), ("test_find", "Search")):
        body = {
            "historyId": name,
            "name": name,
            "fullName": f"tests.{name}",
            "status": "passed",
            "labels": [{"name": "feature", "value": feature}],
        }
        (folder / f"{name}-result.json").write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr(flaky_service, "_cycle_runs", lambda cycle: [run])
    monkeypatch.setattr(flaky_service, "_cases_for_run", lambda r: load_cases(folder.parent.parent))

    found = test_history.list_cycle_tests("unit", label="feature:checkout")
    assert [t.name for t in found.tests] == ["test_pay"]
    assert found.tests[0].labels == ({"name": "feature", "value": "Checkout"},)
    # The search box finds a test by a label's value too.
    assert [t.name for t in test_history.list_cycle_tests("unit", query="search").tests] == [
        "test_find"
    ]
