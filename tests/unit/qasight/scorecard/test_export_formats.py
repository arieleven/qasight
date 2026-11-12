"""``qasight scorecard show --format sarif|html|svg`` (qasight/services/scorecard/export.py)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.repository.db_config import reset_engine_cache
from qasight.services.scorecard.export import _location, badge
from qasight.services.scorecard.report import build_scorecard

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/a.py:10", {"artifactLocation": {"uri": "src/a.py"}, "region": {"startLine": 10}}),
        (
            "./src/a.py:10:4",
            {
                "artifactLocation": {"uri": "src/a.py"},
                "region": {"startLine": 10, "startColumn": 4},
            },
        ),
        ("package.json", {"artifactLocation": {"uri": "package.json"}}),
        ("", None),
        ("https://example.com/x", None),
    ],
)
def test_locations(raw: str, expected: dict | None) -> None:
    got = _location(raw)
    assert (got["physicalLocation"] if got else None) == expected


def test_badge_without_a_score_is_grey() -> None:
    assert badge(None).message == "not scored"


@pytest.fixture
def scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'h.db'}")
    monkeypatch.chdir(tmp_path)
    reset_engine_cache()
    report = tmp_path / "bandit.json"
    report.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "filename": "app/run.py",
                        "line_number": 7,
                        "issue_severity": "HIGH",
                        "issue_confidence": "HIGH",
                        "test_id": "B602",
                        "issue_text": "shell=True <script>",
                    }
                ],
                "metrics": {"_totals": {"loc": 100}},
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["scorecard", "import", "bandit", str(report), "--project", "acme/app", "--version", "1.0"],
    )
    assert result.exit_code == 0, result.output
    yield tmp_path
    reset_engine_cache()


def test_show_writes_sarif_html_and_svg(scored: Path) -> None:
    runner = CliRunner()
    for fmt in ("sarif", "html", "svg"):
        out = scored / f"card.{fmt}"
        result = runner.invoke(
            app, ["scorecard", "show", "--project", "acme/app", "--format", fmt, "-o", str(out)]
        )
        assert result.exit_code == 0, result.output
    sarif = json.loads((scored / "card.sarif").read_text())
    assert sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"] == {
        "startLine": 7
    }
    page = (scored / "card.html").read_text()
    card = build_scorecard("acme/app", "1.0")
    assert card is not None
    assert "<script" not in page.replace("&lt;script&gt;", "")
    assert f">{card.grade}<" in page and "B602" in page and "app/run.py:7" in page
    assert f"{card.grade} {card.score}" in (scored / "card.svg").read_text()


def test_show_refuses_an_unknown_format(scored: Path) -> None:
    result = CliRunner().invoke(app, ["scorecard", "show", "--format", "pdf"])
    assert result.exit_code != 0 and "sarif" in result.output
