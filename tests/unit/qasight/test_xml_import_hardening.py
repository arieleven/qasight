"""Uploaded XML reports are parsed with defusedxml: entity declarations are refused, not expanded."""

from __future__ import annotations

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
