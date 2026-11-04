"""A cycle's tests counted by label: "Checkout: 12 tests, 3 failing", for the cycle page.

Labels say what a test is about (feature, story, epic, severity, owner, tag and
custom ones). They come from the run's results: Allure labels, pytest markers
through allure-pytest, Playwright tags, Behave tags, and a JUnit test case's own
``<properties>`` (:func:`qasight.reporting.allure_cases.case_labels`). This
module reads the cycle's latest run with tests and counts each label's tests,
failures and flaky ones, using the same per-test history as the test list.
"""

from __future__ import annotations

from dataclasses import dataclass

from qasight.services.test_history import DEFAULT_WINDOW, list_cycle_tests

_FAIL = frozenset({"failed", "broken"})
# Labels the breakdown leads with, in this order; the rest follow by name.
_LEADING = ("epic", "feature", "story", "severity", "owner", "tag")


@dataclass(frozen=True)
class LabelCount:
    name: str
    value: str
    tests: int
    failing: int
    flaky: int

    @property
    def filter(self) -> str:
        """The value ``?label=`` takes to list these tests."""
        return f"{self.name}:{self.value}"


@dataclass(frozen=True)
class CycleLabels:
    cycle: str
    runs_considered: int
    # Tests with at least one label, out of every test in the window.
    labelled_tests: int
    total_tests: int
    labels: tuple[LabelCount, ...]


def _order(name: str) -> tuple[int, str]:
    return (_LEADING.index(name), "") if name in _LEADING else (len(_LEADING), name)


def cycle_labels(cycle: str, *, window: int = DEFAULT_WINDOW) -> CycleLabels:
    """Each label in ``cycle``'s recent tests with its test, failing and flaky counts.

    A test counts as failing when its latest outcome failed. Labels group by
    name (features first, then stories, severities...), most failing first.
    """
    found = list_cycle_tests(cycle, window=window)
    counts: dict[tuple[str, str], list[int]] = {}
    labelled = 0
    for test in found.tests:
        if test.labels:
            labelled += 1
        for lab in {(str(lab.get("name", "")), str(lab.get("value", ""))) for lab in test.labels}:
            row = counts.setdefault(lab, [0, 0, 0])
            row[0] += 1
            row[1] += test.last_status in _FAIL
            row[2] += test.flaky
    labels = [
        LabelCount(name=name, value=value, tests=t, failing=f, flaky=k)
        for (name, value), (t, f, k) in counts.items()
    ]
    labels.sort(key=lambda c: (_order(c.name), -c.failing, -c.tests, c.value.casefold()))
    return CycleLabels(
        cycle=cycle,
        runs_considered=found.runs_considered,
        labelled_tests=labelled,
        total_tests=len(found.tests),
        labels=tuple(labels),
    )
