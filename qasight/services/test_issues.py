"""Issues and the tests that cover them: "PAY-7: 3 tests, 1 failing", for the cycle page.

A test points at an issue (a bug, story or requirement in Jira, GitHub, Linear...)
through an issue link (Allure ``issue`` / ``tms`` links, Playwright annotations of
type ``issue``, a JUnit ``issue`` property holding a URL) or an ``issue`` label
without a URL (``record_property("jira", "PAY-8")``). See
:func:`qasight.reporting.allure_cases.case_links` and the JUnit importer.

This module reads the same per-test history as the test list and groups the
tests by issue, so a reader sees which issues have red tests right now: an
issue is red when one of its tests failed in its latest run. Read-only: it never
calls the issue tracker.
"""

from __future__ import annotations

from dataclasses import dataclass

from qasight.services.test_history import DEFAULT_WINDOW, TestSummary, list_cycle_tests

_FAIL = frozenset({"failed", "broken"})
ISSUE_LINK_TYPES = frozenset({"issue", "tms"})
# Tests listed per issue; the counts cover all of them.
TESTS_PER_ISSUE = 20


@dataclass(frozen=True)
class IssueTest:
    key: str
    name: str
    last_status: str
    flaky: bool


@dataclass(frozen=True)
class IssueStatus:
    # What the results call it: "PAY-7", or the URL when the link has no name.
    issue: str
    url: str | None
    tests: int
    failing: int
    flaky: int
    examples: tuple[IssueTest, ...]

    @property
    def state(self) -> str:
        """``failing`` when a test failed in its latest run, ``flaky``, else ``passing``."""
        if self.failing:
            return "failing"
        return "flaky" if self.flaky else "passing"


@dataclass(frozen=True)
class CycleIssues:
    cycle: str
    runs_considered: int
    # Tests that point at an issue, out of every test in the window.
    linked_tests: int
    total_tests: int
    issues: tuple[IssueStatus, ...]


def issues_of(test: TestSummary) -> dict[str, str | None]:
    """``{issue: url}`` for one test: its issue links, then ``issue`` labels without one."""
    found: dict[str, str | None] = {}
    for link in test.links:
        if str(link.get("type", "")).lower() not in ISSUE_LINK_TYPES:
            continue
        url = str(link.get("url") or "") or None
        name = str(link.get("name") or "").strip() or (url or "")
        if name:
            found.setdefault(name, url)
    for label in test.labels:
        if str(label.get("name", "")).lower() == "issue":
            value = str(label.get("value") or "").strip()
            if value and not any(value.casefold() == k.casefold() for k in found):
                found[value] = None
    return found


def cycle_issues(cycle: str, *, window: int = DEFAULT_WINDOW) -> CycleIssues:
    """Each issue ``cycle``'s tests point at, failing ones first, with their tests' state."""
    found = list_cycle_tests(cycle, window=window)
    grouped: dict[str, tuple[str, str | None, list[TestSummary]]] = {}
    linked = 0
    for test in found.tests:
        issues = issues_of(test)
        if issues:
            linked += 1
        for name, url in issues.items():
            key = name.casefold()
            label, known_url, tests = grouped.get(key, (name, url, []))
            tests.append(test)
            grouped[key] = (label, known_url or url, tests)

    out: list[IssueStatus] = []
    for label, url, tests in grouped.values():
        tests.sort(key=lambda t: (t.last_status not in _FAIL, not t.flaky, t.name))
        out.append(
            IssueStatus(
                issue=label,
                url=url,
                tests=len(tests),
                failing=sum(1 for t in tests if t.last_status in _FAIL),
                flaky=sum(1 for t in tests if t.flaky),
                examples=tuple(
                    IssueTest(key=t.key, name=t.name, last_status=t.last_status, flaky=t.flaky)
                    for t in tests[:TESTS_PER_ISSUE]
                ),
            )
        )
    out.sort(key=lambda i: (-i.failing, -i.flaky, i.issue.casefold()))
    return CycleIssues(
        cycle=cycle,
        runs_considered=found.runs_considered,
        linked_tests=linked,
        total_tests=len(found.tests),
        issues=tuple(out),
    )
