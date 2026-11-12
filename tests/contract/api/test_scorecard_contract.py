"""Contract for ``/api/v1/scorecard``: tools and their switches, a version's scorecard, Markdown, results and scans."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight.repository.db import reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight_api.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.tier_fast]

_TOKEN = "s3cret-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    for name in ("TESTO_AUTH", "TESTO_PROTECT_READS", "TESTO_CORS_ORIGINS", "TESTO_UI_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("qasight.paths.ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr("qasight.paths.STATIC_HISTORY_ROOT", tmp_path / "static" / "history")
    monkeypatch.chdir(tmp_path)
    reset_engine_cache()
    reset_repository_cache()
    yield TestClient(create_app())
    reset_repository_cache()
    reset_engine_cache()


def _result(version: str, tool: str, score: int) -> dict:
    return {
        "project": "acme/app",
        "version": version,
        "tool": tool,
        "score": score,
        "summary": f"{score}",
        "findings": [{"severity": "high", "title": "X"}],
    }


def test_empty_scorecard(client: TestClient) -> None:
    assert client.get("/api/v1/scorecard").json()["status"] == "empty"
    assert client.get("/api/v1/scorecard/projects").json() == {"items": []}
    assert client.get("/api/v1/scorecard/markdown").status_code == 404


def test_tools_and_switches(client: TestClient) -> None:
    items = client.get("/api/v1/scorecard/tools").json()["items"]
    by_id = {t["id"]: t for t in items}
    assert by_id["zap"]["kind"] == "app" and by_id["coverage"]["installed"] is None
    assert by_id["locust"]["enabled"] is False
    assert client.put("/api/v1/scorecard/tools/bandit", json={"enabled": False}).status_code == 401
    resp = client.put("/api/v1/scorecard/tools/bandit", json={"enabled": False}, headers=_AUTH)
    assert resp.status_code == 200 and resp.json()["enabled"] is False
    assert (
        client.put(
            "/api/v1/scorecard/tools/nope", json={"enabled": True}, headers=_AUTH
        ).status_code
        == 404
    )
    assert {t["id"]: t["enabled"] for t in client.get("/api/v1/scorecard/tools").json()["items"]}[
        "bandit"
    ] is False


def test_results_from_clients_make_a_scorecard_with_a_comparison(client: TestClient) -> None:
    assert (
        client.post("/api/v1/scorecard/results", json=_result("1.0.0", "bandit", 90)).status_code
        == 401
    )
    for body in (
        _result("1.0.0", "bandit", 90),
        _result("1.1.0", "bandit", 70),
        _result("1.1.0", "ruff", 100),
    ):
        assert client.post("/api/v1/scorecard/results", json=body, headers=_AUTH).status_code == 200
    card = client.get("/api/v1/scorecard").json()
    assert (card["project"], card["version"], card["previous_version"]) == (
        "acme/app",
        "1.1.0",
        "1.0.0",
    )
    assert card["score"] == 85
    bandit = next(t for t in card["tools"] if t["tool"] == "bandit")
    assert (bandit["score"], bandit["previous_score"], bandit["source"]) == (70, 90, "server")
    older = client.get(
        "/api/v1/scorecard", params={"project": "acme/app", "version": "1.0.0"}
    ).json()
    assert older["previous_version"] is None
    projects = client.get("/api/v1/scorecard/projects").json()["items"]
    assert [v["version"] for v in projects[0]["versions"]] == ["1.1.0", "1.0.0"]
    markdown = client.get("/api/v1/scorecard/markdown", params={"project": "acme/app"}).json()[
        "markdown"
    ]
    assert markdown.startswith("# Release scorecard: acme/app 1.1.0")
    bad = client.post("/api/v1/scorecard/results", json=_result("1", "nope", 1), headers=_AUTH)
    assert bad.status_code == 422


def test_scan_of_a_folder_runs_in_the_background(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    assert client.post("/api/v1/scorecard/scans", json={"target": str(repo)}).status_code == 401
    resp = client.post(
        "/api/v1/scorecard/scans",
        json={"target": str(repo), "project": "r", "version": "1"},
        headers=_AUTH,
    )
    assert resp.status_code == 202
    scan_id = resp.json()["id"]
    state = resp.json()
    for _ in range(100):
        state = client.get(f"/api/v1/scorecard/scans/{scan_id}").json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    # Only language-specific or missing tools: nothing to score, and it says why.
    assert state["state"] in {"failed", "done"}
    assert state["project"] == "r"
    assert state["skipped"]
    assert client.get("/api/v1/scorecard/scans/nope").status_code == 404
    bad = client.post(
        "/api/v1/scorecard/scans", json={"target": "x", "tools": ["nope"]}, headers=_AUTH
    )
    assert bad.status_code == 422


def test_badge_sarif_and_html_from_the_same_scorecard(client: TestClient) -> None:
    assert "not scored" in client.get("/api/v1/scorecard/badge.svg").text
    assert client.get("/api/v1/scorecard/sarif").status_code == 404
    body = _result("1.0.0", "bandit", 60)
    body["findings"] = [
        {
            "severity": "high",
            "title": "B602",
            "detail": "subprocess call with shell=True",
            "location": "app/run.py:12",
        },
        {"severity": "low", "title": "B101", "detail": "assert used"},
    ]
    assert client.post("/api/v1/scorecard/results", json=body, headers=_AUTH).status_code == 200
    card = client.get("/api/v1/scorecard").json()

    svg = client.get("/api/v1/scorecard/badge.svg?label=code")
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert f"{card['grade']} {card['score']}" in svg.text and ">code<" in svg.text
    shields = client.get("/api/v1/scorecard/badge.json").json()
    assert (
        shields["schemaVersion"] == 1 and shields["message"] == f"{card['grade']} {card['score']}"
    )

    sarif = client.get("/api/v1/scorecard/sarif?project=acme/app&version=1.0.0").json()
    assert sarif["version"] == "2.1.0" and len(sarif["runs"]) == 1
    results = sarif["runs"][0]["results"]
    located = results[0]["locations"][0]["physicalLocation"]
    assert results[0]["ruleId"] == "bandit/B602" and results[0]["level"] == "error"
    assert located == {"artifactLocation": {"uri": "app/run.py"}, "region": {"startLine": 12}}
    assert results[1]["level"] == "note"
    assert results[1]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "."
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert rules[0]["properties"]["security-severity"] == "7.5"
