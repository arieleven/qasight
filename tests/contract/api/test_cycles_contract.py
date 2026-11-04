from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight_api.main import create_app
from qasight_api.routes import cycles as cycles_routes

CONFIG_YAML = """\
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
  timeout_s: 600
  workers: 4
cycles:
  smoke:
    description: Quick smoke cycle.
    stages:
      - name: pytest-smoke
        equipment: pytest
        args: [-q, tests]
  full:
    description: Multi-framework cycle.
    stages:
      - name: pytest-stage
        equipment: pytest
        args: [-q, tests]
      - name: behave-stage
        equipment: behave
        args: [features]
"""


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "qasight.yaml"
    path.write_text(CONFIG_YAML, encoding="utf-8")
    return path


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _recorded_run(cycle: str) -> CompletedRunView:
    return CompletedRunView(
        run_id=f"{cycle}-1",
        status=RunStatus.COMPLETED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind="cycle",
        returncode=1,
        wall_duration_ms=1000.0,
        metrics_duration_ms=None,
        total_tests=5,
        passed=1,
        failed=4,
        broken=0,
        skipped=0,
        avg_case_ms=None,
        health_pct=20.0,
        target_repo="/ci/checkout",
        snapshot_dir=None,
        audit_json=None,
        cycle=cycle,
        stage_health=[{"name": "junit", "framework": "junit"}],
    )


@pytest.fixture(autouse=True)
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, CompletedRunView]:
    """Cycles in run history; empty unless a test adds one (the shared test DB has others)."""
    found: dict[str, CompletedRunView] = {}
    monkeypatch.setattr(cycles_routes, "latest_run_per_cycle", lambda: found)
    return found


def test_list_cycles_returns_summaries(client: TestClient, config_path: Path) -> None:
    resp = client.get("/api/v1/cycles", params={"config_path": str(config_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["config_path"] == str(config_path)
    by_name = {item["name"]: item for item in body["items"]}
    assert set(by_name) == {"smoke", "full"}
    assert by_name["smoke"]["stage_count"] == 1
    assert by_name["smoke"]["equipment"] == ["pytest"]
    assert by_name["full"]["stage_count"] == 2
    assert by_name["full"]["equipment"] == ["behave", "pytest"]
    assert by_name["full"]["description"] == "Multi-framework cycle."


def test_get_cycle_detail_resolves_stages(client: TestClient, config_path: Path) -> None:
    resp = client.get("/api/v1/cycles/full", params={"config_path": str(config_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "full"
    assert [s["name"] for s in body["stages"]] == ["pytest-stage", "behave-stage"]
    assert body["stages"][0]["equipment"] == "pytest"
    assert body["stages"][0]["args"] == ["-q", "tests"]
    assert body["stages"][0]["timeout_s"] == 600
    assert body["stages"][0]["workers"] == 4
    # Native behave runs single-process, so it reports no workers at all.
    assert body["stages"][1]["workers"] is None
    assert body["trigger"] is None


def test_get_unknown_cycle_is_404_with_available_names(
    client: TestClient, config_path: Path
) -> None:
    resp = client.get("/api/v1/cycles/nope", params={"config_path": str(config_path)})
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "not_found"
    assert "Unknown cycle: nope" in error["message"]
    assert "full" in error["message"] and "smoke" in error["message"]


def test_missing_config_is_503(client: TestClient, tmp_path: Path) -> None:
    resp = client.get("/api/v1/cycles", params={"config_path": str(tmp_path / "qasight.yaml")})
    assert resp.status_code == 503
    error = resp.json()["error"]
    assert error["code"] == "infra_failure"
    assert "config error" in error["message"]


def test_cycles_with_only_recorded_runs_are_listed_read_only(
    client: TestClient, config_path: Path, recorded: dict[str, CompletedRunView]
) -> None:
    recorded["web"] = _recorded_run("web")
    recorded["smoke"] = _recorded_run("smoke")  # in the config too: listed once, as config

    items = client.get("/api/v1/cycles", params={"config_path": str(config_path)}).json()["items"]

    assert [(i["name"], i["source"]) for i in items] == [
        ("smoke", "config"),
        ("full", "config"),
        ("web", "history"),
    ]
    assert items[2]["equipment"] == ["junit"]
    assert items[2]["stage_count"] == 1


def test_list_cycles_carries_the_latest_run(
    client: TestClient, config_path: Path, recorded: dict[str, CompletedRunView]
) -> None:
    """Each card shows its last result without a second request per cycle."""
    recorded["smoke"] = _recorded_run("smoke")
    recorded["web"] = _recorded_run("web")

    items = client.get("/api/v1/cycles", params={"config_path": str(config_path)}).json()["items"]
    by_name = {item["name"]: item for item in items}

    assert by_name["full"]["latest_run"] is None
    latest = by_name["smoke"]["latest_run"]
    assert latest["run_id"] == "smoke-1"
    assert latest["returncode"] == 1
    assert latest["health_pct"] == 20.0
    assert (latest["passed"], latest["failed"], latest["total_tests"]) == (1, 4, 5)
    assert latest["branch"] is None and latest["commit"] is None
    assert by_name["web"]["latest_run"]["run_id"] == "web-1"


def test_recorded_cycle_has_a_detail_page(
    client: TestClient, config_path: Path, recorded: dict[str, CompletedRunView]
) -> None:
    recorded["web"] = _recorded_run("web")

    body = client.get("/api/v1/cycles/web", params={"config_path": str(config_path)}).json()

    assert body["source"] == "history"
    assert body["stages"] == [
        {
            "name": "junit",
            "equipment": "junit",
            "target_repo": "/ci/checkout",
            "args": [],
            "timeout_s": None,
            "workers": None,
        }
    ]
    assert body["trigger"] is None


def test_no_config_still_lists_recorded_cycles(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded: dict[str, CompletedRunView],
) -> None:
    # `qasight ui` over ingested runs, in a directory with no qasight.yaml.
    monkeypatch.chdir(tmp_path)
    recorded["web"] = _recorded_run("web")

    body = client.get("/api/v1/cycles").json()

    assert body["config_path"] is None
    assert [i["name"] for i in body["items"]] == ["web"]
    assert client.get("/api/v1/cycles/web").status_code == 200
    assert client.get("/api/v1/cycles/nope").json()["error"]["message"] == (
        "Unknown cycle: nope. Available cycles: web"
    )
