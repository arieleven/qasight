"""Uploaded XML reports are parsed with defusedxml: entity declarations are refused, not expanded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.reporting.junit_import import import_junit_reports
from qasight.reporting.robot_import import robot_results_for_file
from qasight.reporting.trx_import import trx_results_for_file

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

_BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<{root}><x>&lol2;</x></{root}>
"""


def test_junit_with_entities_is_unreadable_not_expanded(tmp_path: Path) -> None:
    (tmp_path / "bomb.xml").write_text(_BOMB.format(root="testsuites"), encoding="utf-8")
    (tmp_path / "ok.xml").write_text(
        '<testsuite name="s" tests="1"><testcase classname="c" name="t"/></testsuite>',
        encoding="utf-8",
    )
    result = import_junit_reports(
        target_repo=tmp_path, patterns=("*.xml",), results_dir=tmp_path / "out"
    )
    assert result.unreadable == 1
    assert result.tests == 1
    assert any(e.startswith("bomb.xml:") for e in result.errors)


@pytest.mark.parametrize(
    ("reader", "root"),
    [(trx_results_for_file, "TestRun"), (robot_results_for_file, "robot")],
)
def test_trx_and_robot_refuse_entities(tmp_path: Path, reader, root: str) -> None:  # noqa: ANN001
    path = tmp_path / "report.xml"
    path.write_text(_BOMB.format(root=root), encoding="utf-8")
    with pytest.raises(ValueError):
        reader(path, tool="command")


@pytest.mark.parametrize(
    ("report", "kind"),
    [
        (
            {
                "testResults": [
                    {
                        "name": "a",
                        "assertionResults": [
                            {"title": "t", "status": "failed", "failureMessages": 5}
                        ],
                    }
                ]
            },
            "Jest/Vitest",
        ),
        (
            {"suites": [{"title": "s", "specs": [{"title": "t", "tests": [{"results": 3}]}]}]},
            "Playwright",
        ),
    ],
)
def test_a_json_report_with_a_wrong_typed_field_says_so(
    tmp_path: Path, report: dict[str, object], kind: str
) -> None:
    (tmp_path / "r.json").write_text(json.dumps(report), encoding="utf-8")
    result = import_junit_reports(
        target_repo=tmp_path, patterns=("*.json",), results_dir=tmp_path / "out"
    )
    assert result.unreadable == 1
    [error] = result.errors
    assert f"looks like a {kind} report, but a field is missing or has an unexpected type" in error
    assert "could not be read" not in error
