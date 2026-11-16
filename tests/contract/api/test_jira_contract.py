"""Filing a failing test in Jira from the run page (``/api/v1/jira``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from qasight.repository.db_config import reset_engine_cache
from qasight.services import jira_issues
from qasight.services.jira_issues import JiraClient, JiraConfig, JiraError, summary_for
from qasight.services.triage import TriageStore
from qasight_api.main import create_app

_TOKEN = "jira-test-token"


class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self) -> Any:
        return self._body


class _FakeJira:
    def __init__(self, status: int = 201) -> None:
        self.headers: dict[str, str] = {}
        self.auth: tuple[str, str] | None = None
        self.sent: list[dict[str, Any]] = []
        self.status = status

    def post(self, url: str, json: dict[str, Any], timeout: float) -> _Resp:
        self.sent.append({"url": url, "json": json})
        if self.status != 201:
            return _Resp(self.status, {"errorMessages": ["project is required"], "errors": {}})
        return _Resp(201, {"key": "SHOP-12"})


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    monkeypatch.delenv("TESTO_AUTH", raising=False)
    reset_engine_cache()
    app = TestClient(create_app())
    app.headers["Authorization"] = f"Bearer {_TOKEN}"
    yield app
    reset_engine_cache()


def test_off_until_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("TESTO_JIRA_URL", "TESTO_JIRA_TOKEN", "TESTO_JIRA_PROJECT"):
        monkeypatch.delenv(env, raising=False)
    assert client.get("/api/v1/jira").json() == {"enabled": False, "project": None, "url": None}
    resp = client.post("/api/v1/jira/issues", json={"cycle": "shop", "test": "t"})
    assert resp.status_code == 409


def test_filing_creates_the_issue_and_marks_the_cause(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESTO_JIRA_URL", "https://team.atlassian.net/")
    monkeypatch.setenv("TESTO_JIRA_EMAIL", "qa@example.com")
    monkeypatch.setenv("TESTO_JIRA_TOKEN", "t")
    monkeypatch.setenv("TESTO_JIRA_PROJECT", "SHOP")
    fake = _FakeJira()
    monkeypatch.setattr(jira_issues.requests, "Session", lambda: fake)
    assert client.get("/api/v1/jira").json()["project"] == "SHOP"

    body = {
        "cycle": "shop",
        "test": "test_total",
        "message": "AssertionError: total 3 != 4",
        "run_id": "r1",
    }
    resp = client.post("/api/v1/jira/issues", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json() == {
        "key": "SHOP-12",
        "url": "https://team.atlassian.net/browse/SHOP-12",
        "cause_marked": True,
    }
    sent = fake.sent[0]
    assert sent["url"] == "https://team.atlassian.net/rest/api/3/issue"
    assert sent["json"]["fields"]["summary"] == "test_total fails: AssertionError: total 3 != 4"
    assert sent["json"]["fields"]["project"] == {"key": "SHOP"}
    assert fake.auth == ("qa@example.com", "t")

    (cause,) = TriageStore().list("shop")
    assert cause.cause == "product_bug" and cause.note.startswith("SHOP-12 ")

    # The cause is marked once; a second issue for the same message leaves it.
    assert client.post("/api/v1/jira/issues", json=body).json()["cause_marked"] is False


def test_jira_errors_are_passed_on() -> None:
    cfg = JiraConfig(url="https://j", email="", token="pat", project="X")
    fake = _FakeJira(status=400)
    with pytest.raises(JiraError, match="project is required"):
        JiraClient(cfg, session=fake).create_issue(
            summary="s", cycle="c", test="t", message="", run_url=None
        )  # type: ignore[arg-type]
    assert fake.headers["Authorization"] == "Bearer pat"
    assert summary_for("t", "") == "t fails"
