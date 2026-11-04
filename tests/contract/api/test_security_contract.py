"""Contract for the API's optional bearer token and CORS defaults (``qasight_api/security.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight_api.main import create_app, run
from qasight_api.security import is_loopback_host

_ADHOC = "/api/v1/adhoc-executions"


class _FakeManager:
    def __init__(self) -> None:
        self.calls = 0

    def create_adhoc_execution(self, **_kwargs):  # noqa: ANN003
        self.calls += 1
        raise AssertionError("auth should have rejected this request first")


def _client(manager: _FakeManager | None = None) -> TestClient:
    from qasight_api.dependencies import get_cycle_execution_manager

    app = create_app()
    if manager is not None:
        app.dependency_overrides[get_cycle_execution_manager] = lambda: manager
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TESTO_API_TOKEN", "TESTO_CORS_ORIGINS", "TESTO_PROTECT_READS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("TESTO_API_TOKEN", "s3cret")
    return "s3cret"


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Basic s3cret", "s3cret"])
def test_mutating_request_without_valid_token_is_401(token: str, header: str | None) -> None:
    manager = _FakeManager()
    headers = {"Authorization": header} if header else {}
    resp = _client(manager).post(_ADHOC, json={"framework": "pytest"}, headers=headers)
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    assert resp.json()["error"]["code"] == "unauthorized"
    assert manager.calls == 0


def test_mutating_request_with_token_reaches_the_route(token: str) -> None:
    # An empty body fails validation, which proves auth let the request through.
    resp = _client().post(_ADHOC, json={}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


def test_reads_stay_open_with_token_set(token: str) -> None:
    assert _client().get("/api/v1/health/live").status_code == 200


def test_no_token_configured_means_no_auth() -> None:
    assert _client().post(_ADHOC, json={}).status_code == 422


def test_foreign_origin_cannot_mutate_even_without_token() -> None:
    # A cross-site "simple" POST skips the CORS preflight, so the server must refuse it itself.
    manager = _FakeManager()
    client = _client(manager)
    resp = client.post(_ADHOC, json={}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    assert manager.calls == 0
    allowed = client.post(_ADHOC, json={}, headers={"Origin": "http://localhost:5173"})
    assert allowed.status_code == 422
    # Reads from any origin stay open (CORS still decides whether the browser may see them).
    read = client.get("/api/v1/health/live", headers={"Origin": "https://evil.example"})
    assert read.status_code == 200


def test_token_tolerates_surrounding_whitespace_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_API_TOKEN", "s3cret\n")
    resp = _client().post(_ADHOC, json={}, headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 422


def _preflight(client: TestClient, origin: str):  # noqa: ANN202
    return client.options(
        _ADHOC,
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )


def test_cors_defaults_to_vite_dev_server_without_credentials() -> None:
    client = _client()
    allowed = _preflight(client, "http://localhost:5173")
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in allowed.headers
    denied = _preflight(client, "https://evil.example")
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_cors_explicit_origins_allow_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_CORS_ORIGINS", "https://qa.internal, http://localhost:4173")
    client = _client()
    resp = _preflight(client, "https://qa.internal")
    assert resp.headers["access-control-allow-origin"] == "https://qa.internal"
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert _preflight(client, "http://localhost:5173").status_code == 400


def test_cors_wildcard_never_allows_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_CORS_ORIGINS", "*")
    resp = _preflight(_client(), "https://anywhere.example")
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in resp.headers


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),
        ("myhost", False),
    ],
)
def test_is_loopback_host(host: str, loopback: bool) -> None:
    assert is_loopback_host(host) is loopback


def _run_without_uvicorn(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import sys
    import types

    calls: list[str] = []
    fake = types.SimpleNamespace(run=lambda *_a, **kw: calls.append(kw["host"]))
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    return calls


def test_public_bind_without_token_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _run_without_uvicorn(monkeypatch)
    monkeypatch.setenv("TESTO_API_HOST", "0.0.0.0")
    monkeypatch.delenv("TESTO_API_TOKEN", raising=False)
    monkeypatch.delenv("TESTO_ALLOW_UNAUTHENTICATED", raising=False)
    assert run() == 2
    assert calls == []


@pytest.mark.parametrize(
    "env",
    [
        {"TESTO_API_TOKEN": "s3cret"},
        {"TESTO_ALLOW_UNAUTHENTICATED": "1"},
        {"TESTO_API_HOST": "127.0.0.1"},
    ],
)
def test_bind_starts_with_token_opt_out_or_loopback(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    calls = _run_without_uvicorn(monkeypatch)
    monkeypatch.setenv("TESTO_API_HOST", "0.0.0.0")
    monkeypatch.delenv("TESTO_API_TOKEN", raising=False)
    monkeypatch.delenv("TESTO_ALLOW_UNAUTHENTICATED", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert run() == 0
    assert len(calls) == 1


def test_read_config_path_only_loads_config_file_names(tmp_path: Path) -> None:
    secret = tmp_path / "notes.txt"
    secret.write_text("password: hunter2\n[ -z x ] && return\n", encoding="utf-8")
    client = TestClient(create_app())
    for route in ("/api/v1/cycles", "/api/v1/cycles/smoke"):
        resp = client.get(route, params={"config_path": str(secret)})
        assert resp.status_code == 400
        assert "hunter2" not in resp.text and "notes.txt" not in resp.text


# --- TESTO_PROTECT_READS and the /login session cookie ---------------------------

_READ = "/api/v1/runs"


def _login(client: TestClient, token: str, next_path: str = "/") -> object:
    return client.post(
        "/api/v1/auth/login",
        data={"token": token, "next": next_path},
        follow_redirects=False,
    )


def test_reads_stay_open_by_default_even_with_a_token(token: str) -> None:
    assert _client().get(_READ).status_code == 200


@pytest.fixture
def protected(token: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("TESTO_PROTECT_READS", "1")
    return token


def test_protect_reads_without_a_token_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_PROTECT_READS", "1")
    assert _client().get(_READ).status_code == 200


def test_protected_read_without_session_is_401(protected: str) -> None:
    resp = _client().get(_READ)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert resp.json()["request_id"]


def test_protected_report_file_is_401(protected: str) -> None:
    # /history is a static mount, outside any route dependency.
    assert _client().get("/history/some-run/report.html").status_code == 401


def test_protected_page_redirects_a_browser_to_login(protected: str) -> None:
    resp = _client().get(
        "/history/run-1/allure/index.html?x=1",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert (
        resp.headers["location"] == "/login?next=%2Fhistory%2Frun-1%2Fallure%2Findex.html%3Fx%3D1"
    )


def test_health_and_login_page_stay_open(protected: str) -> None:
    client = _client()
    assert client.get("/api/v1/health/live").status_code == 200
    page = client.get("/login?next=/runs/abc")
    assert page.status_code == 200
    assert 'name="next" value="/runs/abc"' in page.text


def test_protected_read_with_bearer_token_passes(protected: str) -> None:
    assert _client().get(_READ, headers={"Authorization": f"Bearer {protected}"}).status_code == 200


def test_login_sets_an_httponly_cookie_that_opens_reads(protected: str) -> None:
    client = _client()
    resp = _login(client, protected, "/runs/abc")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/runs/abc"
    cookie = resp.headers["set-cookie"]
    assert "testo_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert protected not in cookie  # derived, never the token itself
    assert client.get(_READ).status_code == 200


def test_session_cookie_also_authorizes_writes(protected: str) -> None:
    client = _client()
    _login(client, protected)
    # 422 (empty body) means auth let it through.
    assert client.post(_ADHOC, json={}).status_code == 422


def test_wrong_token_does_not_sign_in(protected: str) -> None:
    client = _client()
    resp = _login(client, "nope")
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers
    assert client.get(_READ).status_code == 401


def test_changing_the_token_ends_old_sessions(
    protected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    _login(client, protected)
    monkeypatch.setenv("TESTO_API_TOKEN", "rotated")
    assert client.get(_READ).status_code == 401


@pytest.mark.parametrize(
    "target", ["https://evil.example", "//evil.example", "/\\evil.example", "runs"]
)
def test_login_never_redirects_off_the_server(protected: str, target: str) -> None:
    resp = _login(_client(), protected, target)
    assert resp.headers["location"] == "/"


def test_logout_clears_the_session(protected: str) -> None:
    client = _client()
    _login(client, protected)
    resp = client.post("/api/v1/auth/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert client.get(_READ).status_code == 401


def test_login_from_a_foreign_origin_is_refused(protected: str) -> None:
    resp = _client().post(
        "/api/v1/auth/login",
        data={"token": protected},
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_every_response_gets_nosniff_and_same_origin_framing() -> None:
    resp = _client().get("/api/v1/health/live")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["Content-Security-Policy"] == "frame-ancestors 'self'"


def test_refusals_get_the_headers_too(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_PROTECT_READS", "1")
    resp = _client().get("/api/v1/cycles")
    assert resp.status_code == 401
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "/history/run-1/allure-results/0b7f-attachment.html",
        "/history/run-1/allure/data/attachments/1a2b.html",
        "/history/run-1/artifacts/web/junit/allure-results/junit/page.html",
    ],
)
def test_report_attachments_are_sandboxed(path: str) -> None:
    resp = _client().get(path)
    assert resp.headers["Content-Security-Policy"] == "sandbox; frame-ancestors 'self'"


def test_report_pages_are_not_sandboxed() -> None:
    # Allure's index.html needs script and its own origin to load data/*.json.
    resp = _client().get("/history/run-1/allure/index.html")
    assert resp.headers["Content-Security-Policy"] == "frame-ancestors 'self'"


def test_wildcard_origins_do_not_let_any_page_write_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TESTO_CORS_ORIGINS", "*")
    manager = _FakeManager()
    resp = _client(manager).post(
        _ADHOC, json={"framework": "pytest"}, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403
    assert manager.calls == 0


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost"])
def test_tokenless_api_answers_loopback_names(host: str) -> None:
    resp = _client().get("/api/v1/health/live", headers={"Host": host})
    assert resp.status_code == 200


def test_tokenless_api_refuses_a_rebinding_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # DNS rebinding: the page's own name now points at 127.0.0.1.
    monkeypatch.delenv("TESTO_ALLOWED_HOSTS", raising=False)
    resp = _client().get("/api/v1/runs", headers={"Host": "rebind.evil.example:8000"})
    assert resp.status_code == 421
    assert "TESTO_ALLOWED_HOSTS" in resp.json()["error"]["message"]


def test_allowed_hosts_and_token_lift_the_host_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_ALLOWED_HOSTS", "testo.lan")
    assert _client().get("/api/v1/health/live", headers={"Host": "testo.lan"}).status_code == 200
    monkeypatch.setenv("TESTO_API_TOKEN", "s3cret")
    assert _client().get("/api/v1/health/live", headers={"Host": "other.lan"}).status_code == 200
    monkeypatch.delenv("TESTO_API_TOKEN")
    monkeypatch.setenv("TESTO_ALLOW_UNAUTHENTICATED", "1")
    assert _client().get("/api/v1/health/live", headers={"Host": "other.lan"}).status_code == 200
