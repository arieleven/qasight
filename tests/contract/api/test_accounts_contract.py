"""Contract for ``TESTO_AUTH=accounts``: per-user sign-in, roles and the audit log."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight.accounts import AccountError, AccountStore, hash_password, verify_password
from qasight.repository.db_config import reset_engine_cache
from qasight_api.login_throttle import LoginThrottle, login_throttle
from qasight_api.main import create_app, run
from qasight_api.security import account_store

_ADHOC = "/api/v1/adhoc-executions"
_READ = "/api/v1/runs"
_TOKEN = "s3cret-token"
_PASSWORD = "correct horse battery"


@pytest.fixture
def accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AccountStore]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    monkeypatch.setenv("TESTO_AUTH", "accounts")
    monkeypatch.delenv("TESTO_PROTECT_READS", raising=False)
    monkeypatch.delenv("TESTO_CORS_ORIGINS", raising=False)
    reset_engine_cache()
    account_store.cache_clear()
    login_throttle.reset()
    store = account_store()
    for name, role in (("vera", "viewer"), ("rui", "runner"), ("ada", "admin")):
        store.add(name, _PASSWORD, role)
    yield store
    account_store.cache_clear()
    reset_engine_cache()


def _signed_in(username: str, password: str = _PASSWORD) -> TestClient:
    client = TestClient(create_app())
    resp = client.post(
        "/api/v1/auth/login",
        data={"username": username, "token": password, "next": "/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return client


def test_password_hashes_are_salted_and_verify() -> None:
    first, second = hash_password("a long password"), hash_password("a long password")
    assert first != second and first.startswith("scrypt$")
    assert verify_password("a long password", first)
    assert not verify_password("a long passwore", first)
    assert not verify_password("anything", "not-a-hash")


def test_store_rejects_bad_names_short_passwords_and_unknown_roles(
    accounts: AccountStore,
) -> None:
    with pytest.raises(AccountError):
        accounts.add("has space", _PASSWORD)
    with pytest.raises(AccountError):
        accounts.add("short", "123")
    with pytest.raises(AccountError):
        accounts.add("eve", _PASSWORD, "root")
    with pytest.raises(AccountError, match="already exists"):
        accounts.add("vera", _PASSWORD)


def test_reads_need_a_session_in_accounts_mode(accounts: AccountStore) -> None:
    assert TestClient(create_app()).get(_READ).status_code == 401


def test_login_page_asks_for_a_username(accounts: AccountStore) -> None:
    page = TestClient(create_app()).get("/login").text
    assert 'name="username"' in page


def test_wrong_password_is_refused_and_audited(accounts: AccountStore) -> None:
    client = TestClient(create_app())
    resp = client.post(
        "/api/v1/auth/login",
        data={"username": "vera", "token": "wrong password", "next": "/"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert client.get(_READ).status_code == 401
    entry = accounts.recent(1)[0]
    assert (entry.username, entry.path, entry.status) == ("vera", "/api/v1/auth/login", 401)


def test_a_viewer_reads_but_cannot_start_runs(accounts: AccountStore) -> None:
    client = _signed_in("vera")
    assert client.get(_READ).status_code == 200
    resp = client.post(_ADHOC, json={})
    assert resp.status_code == 403
    assert "runner" in resp.json()["error"]["message"]


def test_a_runner_starts_runs_but_cannot_change_settings(accounts: AccountStore) -> None:
    client = _signed_in("rui")
    assert client.post(_ADHOC, json={}).status_code == 422  # past auth, body invalid
    assert client.put("/api/v1/ai/config", json={}).status_code == 403


def test_an_admin_changes_settings_and_reads_the_audit_log(accounts: AccountStore) -> None:
    runner = _signed_in("rui")
    runner.post(_ADHOC, json={})
    admin = _signed_in("ada")
    assert admin.put("/api/v1/ai/config", json={}).status_code != 403
    assert _signed_in("vera").get("/api/v1/audit").status_code == 403

    log = admin.get("/api/v1/audit").json()
    writes = [(e["username"], e["method"], e["path"], e["status"]) for e in log]
    assert ("rui", "POST", _ADHOC, 422) in writes
    assert any(e[0] == "ada" and e[2] == "/api/v1/ai/config" for e in writes)


def test_the_token_still_works_as_an_admin(accounts: AccountStore) -> None:
    client = TestClient(create_app())
    headers = {"Authorization": f"Bearer {_TOKEN}"}
    assert client.get(_READ, headers=headers).status_code == 200
    assert client.put("/api/v1/ai/config", json={}, headers=headers).status_code != 403
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me == {"auth_mode": "accounts", "signed_in": True, "username": "token", "role": "admin"}
    # A blank username on the login form means "sign in with the token".
    resp = client.post(
        "/api/v1/auth/login", data={"token": _TOKEN, "next": "/"}, follow_redirects=False
    )
    assert resp.status_code == 303


def test_me_reports_the_signed_in_user(accounts: AccountStore) -> None:
    assert _signed_in("rui").get("/api/v1/auth/me").json() == {
        "auth_mode": "accounts",
        "signed_in": True,
        "username": "rui",
        "role": "runner",
    }


def test_a_role_change_signs_the_user_out(accounts: AccountStore) -> None:
    client = _signed_in("rui")
    accounts.set_role("rui", "viewer")
    assert client.get(_READ).status_code == 401
    assert _signed_in("rui").post(_ADHOC, json={}).status_code == 403


def test_a_removed_user_is_signed_out(accounts: AccountStore) -> None:
    client = _signed_in("vera")
    accounts.remove("vera")
    assert client.get(_READ).status_code == 401


def test_adding_a_removed_name_again_does_not_revive_old_cookies(
    accounts: AccountStore,
) -> None:
    client = _signed_in("vera")
    accounts.remove("vera")
    # A new person gets the name; their row starts at session version 1 again.
    accounts.add("vera", _PASSWORD, "viewer")
    assert client.get(_READ).status_code == 401
    assert _signed_in("vera").get(_READ).status_code == 200


def test_a_tampered_cookie_is_refused(accounts: AccountStore) -> None:
    client = _signed_in("vera")
    value = client.cookies["testo_session"]
    encoded, _, sig = value.partition(".")
    import base64

    forged = base64.urlsafe_b64encode(b"ada:1:9999999999").decode().rstrip("=")
    client.cookies.set("testo_session", f"{forged}.{sig}")
    assert client.get(_READ).status_code == 401


def test_token_mode_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    monkeypatch.delenv("TESTO_AUTH", raising=False)
    monkeypatch.delenv("TESTO_PROTECT_READS", raising=False)
    client = TestClient(create_app())
    assert client.get(_READ).status_code == 200  # reads stay open, as before
    assert 'name="username"' not in client.get("/login").text
    assert client.get("/api/v1/audit").status_code == 404
    assert client.get("/api/v1/auth/me").json()["auth_mode"] == "token"


def test_accounts_mode_without_a_token_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TESTO_AUTH", "accounts")
    monkeypatch.delenv("TESTO_API_TOKEN", raising=False)
    monkeypatch.setenv("TESTO_API_HOST", "127.0.0.1")
    assert run([]) == 2
    assert "TESTO_AUTH=accounts needs TESTO_API_TOKEN" in capsys.readouterr().err


def _attempt(client: TestClient, username: str, password: str):  # noqa: ANN202
    return client.post(
        "/api/v1/auth/login",
        data={"username": username, "token": password, "next": "/"},
        follow_redirects=False,
    )


def test_repeated_wrong_passwords_lock_the_name_for_a_while(
    accounts: AccountStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app())
    for _ in range(5):
        assert _attempt(client, "vera", "wrong password").status_code == 401
    locked = _attempt(client, "vera", _PASSWORD)  # even the right password waits
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert accounts.recent(1)[0].status == 429
    # Other names are not affected.
    assert _attempt(client, "rui", _PASSWORD).status_code == 303


def test_a_successful_sign_in_clears_the_count(accounts: AccountStore) -> None:
    client = TestClient(create_app())
    for _ in range(4):
        _attempt(client, "vera", "wrong password")
    assert _attempt(client, "vera", _PASSWORD).status_code == 303
    for _ in range(4):
        _attempt(client, "vera", "wrong password")
    assert _attempt(client, "vera", _PASSWORD).status_code == 303


def test_throttle_window_and_address_limit() -> None:
    now = [1000.0]
    throttle = LoginThrottle(max_failures=2, window_s=60, clock=lambda: now[0])
    throttle.failed("a", "1.2.3.4")
    assert throttle.retry_after("a", "1.2.3.4") == 0
    throttle.failed("A ", "1.2.3.4")  # names compare case- and space-insensitively
    assert throttle.retry_after("a", "1.2.3.4") == 61
    now[0] += 60
    assert throttle.retry_after("a", "1.2.3.4") == 0  # the window slid past both
    # One address spraying many names hits the address limit (4x the name limit).
    for i in range(8):
        throttle.failed(f"user{i}", "5.6.7.8")
    assert throttle.retry_after("someone-new", "5.6.7.8") > 0
    assert throttle.retry_after("someone-new", "9.9.9.9") == 0
