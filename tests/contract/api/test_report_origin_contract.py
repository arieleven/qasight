"""Contract for ``TESTO_REPORTS_ORIGIN``: report pages served from their own origin."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from qasight.paths import STATIC_HISTORY_ROOT
from qasight_api.main import create_app

_TOKEN = "s3cret-token"
_REPORTS = "http://reports.test"
# One run folder per process: pytest-xdist workers share STATIC_HISTORY_ROOT,
# and one worker's teardown must not delete the page another is reading.
_RUN = f"report-origin-run-{os.getpid()}"
_PAGE = f"/history/{_RUN}/allure/index.html"


@pytest.fixture
def report_file() -> Iterator[Path]:
    run_dir = Path(STATIC_HISTORY_ROOT) / _RUN
    page = run_dir / "allure" / "index.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("<h1>report</h1>", encoding="utf-8")
    yield page
    shutil.rmtree(run_dir, ignore_errors=True)


@pytest.fixture
def split(monkeypatch: pytest.MonkeyPatch, report_file: Path) -> None:
    monkeypatch.setenv("TESTO_REPORTS_ORIGIN", _REPORTS)
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    monkeypatch.setenv("TESTO_PROTECT_READS", "1")
    monkeypatch.delenv("TESTO_AUTH", raising=False)
    monkeypatch.delenv("TESTO_CORS_ORIGINS", raising=False)


def _api() -> TestClient:
    return TestClient(create_app(), base_url="http://testo.test")


def _reports() -> TestClient:
    return TestClient(create_app(), base_url=_REPORTS)


def _signed_in_api() -> TestClient:
    client = _api()
    resp = client.post(
        "/api/v1/auth/login", data={"token": _TOKEN, "next": "/"}, follow_redirects=False
    )
    assert resp.status_code == 303
    return client


def test_off_by_default_serves_reports_on_the_api_host(
    monkeypatch: pytest.MonkeyPatch, report_file: Path
) -> None:
    monkeypatch.delenv("TESTO_REPORTS_ORIGIN", raising=False)
    monkeypatch.delenv("TESTO_PROTECT_READS", raising=False)
    assert _api().get(_PAGE).text == "<h1>report</h1>"


def test_api_host_sends_a_signed_in_viewer_to_the_reports_host(split: None) -> None:
    resp = _signed_in_api().get(_PAGE, follow_redirects=False)
    assert resp.status_code == 303
    location = urlsplit(resp.headers["location"])
    assert f"{location.scheme}://{location.netloc}" == _REPORTS
    assert location.path == "/_report-auth"
    assert parse_qs(location.query)["next"] == [_PAGE]


def test_api_host_sends_a_stranger_to_login_first(split: None) -> None:
    resp = _api().get(_PAGE, headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_ticket_buys_a_reports_cookie_that_opens_the_report(split: None) -> None:
    hop = urlsplit(_signed_in_api().get(_PAGE, follow_redirects=False).headers["location"])
    reports = _reports()
    exchanged = reports.get(f"{hop.path}?{hop.query}", follow_redirects=False)
    assert exchanged.status_code == 303
    assert exchanged.headers["location"] == _PAGE
    cookie = exchanged.headers["set-cookie"]
    assert "testo_reports=" in cookie and "HttpOnly" in cookie
    assert reports.get(_PAGE).text == "<h1>report</h1>"


def test_reports_host_without_its_cookie_is_401(split: None) -> None:
    assert _reports().get(_PAGE).status_code == 401
    # The API host's own session cookie means nothing there.
    reports = _reports()
    reports.cookies.set("testo_session", _signed_in_api().cookies["testo_session"])
    assert reports.get(_PAGE).status_code == 401


def test_a_forged_or_stale_ticket_is_refused(split: None) -> None:
    assert _reports().get("/_report-auth?ticket=abc.def&next=" + _PAGE).status_code == 401


def test_reports_host_serves_nothing_but_reports(split: None) -> None:
    reports = _reports()
    assert reports.get("/api/v1/runs").status_code == 404
    assert reports.get("/login").status_code == 404
    assert reports.post("/api/v1/adhoc-executions", json={}).status_code == 404


def test_a_report_page_cannot_write_to_the_api(split: None) -> None:
    # A script on the reports origin calling the API host carries that Origin.
    resp = _signed_in_api().post("/api/v1/adhoc-executions", json={}, headers={"Origin": _REPORTS})
    assert resp.status_code == 403


def test_open_server_redirects_without_a_ticket(
    monkeypatch: pytest.MonkeyPatch, report_file: Path
) -> None:
    monkeypatch.setenv("TESTO_REPORTS_ORIGIN", _REPORTS)
    monkeypatch.delenv("TESTO_API_TOKEN", raising=False)
    monkeypatch.delenv("TESTO_PROTECT_READS", raising=False)
    resp = _api().get(_PAGE, follow_redirects=False)
    assert resp.headers["location"] == _REPORTS + _PAGE
    assert _reports().get(_PAGE).text == "<h1>report</h1>"
