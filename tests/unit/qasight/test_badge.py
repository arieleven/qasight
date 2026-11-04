"""README badge (qasight/services/badge.py, ``qasight badge``, ``/api/v1/badge.*``)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.history.views import CompletedRunView
from qasight.services import badge
from qasight_api.main import create_app

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

RUN = CompletedRunView(
    run_id="r1",
    status=None,
    created_at=0.0,
    started_at=0.0,
    finished_at=0.0,
    test_kind="junit",
    returncode=0,
    wall_duration_ms=0.0,
    metrics_duration_ms=None,
    total_tests=414,
    passed=412,
    failed=0,
    broken=0,
    skipped=2,
    avg_case_ms=None,
    health_pct=100.0,
    target_repo=None,
    snapshot_dir=None,
    audit_json=None,
    cycle="web",
)


def test_message_and_colour() -> None:
    assert badge.badge_for_run(RUN) == badge.Badge("tests", "412 passed", badge.GREEN)
    assert badge.badge_for_run(RUN, flaky=2).message == "412 passed · 2 flaky"
    assert badge.badge_for_run(RUN, flaky=2).color == badge.YELLOW
    red = badge.badge_for_run(replace(RUN, passed=409, failed=2, broken=1), flaky=1)
    assert red == badge.Badge("tests", "3 failed · 409 passed · 1 flaky", badge.RED)
    assert badge.badge_for_run(None).message == "no runs"
    assert badge.badge_for_run(replace(RUN, total_tests=0)).color == badge.GREY


def test_svg_escapes_and_names_itself() -> None:
    svg = badge.render_svg(badge.Badge('<a "x">', "1 passed", badge.GREEN))
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<a " not in svg
    assert "<title>&lt;a &quot;x&quot;&gt;: 1 passed</title>" in svg


def test_shields_endpoint() -> None:
    assert badge.shields_endpoint(badge.Badge("tests", "2 failed · 3 passed", badge.RED)) == {
        "schemaVersion": 1,
        "label": "tests",
        "message": "2 failed · 3 passed",
        "color": "red",
    }


@pytest.fixture
def one_cycle(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    asked: list[str | None] = []

    def fake(cycle: str | None, *, label: str = "tests") -> badge.Badge:
        asked.append(cycle)
        return badge.badge_for_run(RUN, flaky=1, label=label)

    monkeypatch.setattr(badge, "badge_for_cycle", fake)
    monkeypatch.setattr("qasight_api.routes.badge.badge_for_cycle", fake)
    return asked


def test_cli_writes_svg_and_json(tmp_path, one_cycle) -> None:  # type: ignore[no-untyped-def]
    runner = CliRunner()
    out = tmp_path / "public" / "badge.svg"
    result = runner.invoke(app, ["badge", "--cycle", "web", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "412 passed · 1 flaky" in out.read_text(encoding="utf-8")
    assert one_cycle == ["web"]

    result = runner.invoke(app, ["badge", "--format", "json", "--label", "e2e"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["label"] == "e2e"
    assert runner.invoke(app, ["badge", "--format", "png"]).exit_code == 2


def test_api_routes(one_cycle) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(create_app())
    svg = client.get("/api/v1/badge.svg", params={"cycle": "web"})
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.headers["cache-control"] == "max-age=300"
    assert "412 passed" in svg.text
    endpoint = client.get("/api/v1/badge.json").json()
    assert endpoint["color"] == "yellow"
    assert one_cycle == ["web", None]
