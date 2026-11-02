"""Contract for rotating ``TESTO_API_TOKEN``: the previous token and ``TESTO_SESSION_KEY``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from testo_api.login_throttle import login_throttle
from testo_api.main import create_app
from testo_api.security import account_store
from testo_core.repository.db_config import reset_engine_cache

_READ = "/api/v1/runs"
_OLD = "old-token-0123456789"
_NEW = "new-token-9876543210"
_PASSWORD = "correct horse battery"


@pytest.fixture
def accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", _OLD)
    monkeypatch.setenv("TESTO_AUTH", "accounts")
    for name in ("TESTO_API_TOKEN_PREVIOUS", "TESTO_SESSION_KEY", "TESTO_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    reset_engine_cache()
    account_store.cache_clear()
    login_throttle.reset()
    account_store().add("ada", _PASSWORD, "admin")
    yield
    account_store.cache_clear()
    reset_engine_cache()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sign_in() -> TestClient:
    client = TestClient(create_app())
    resp = client.post(
        "/api/v1/auth/login",
        data={"username": "ada", "token": _PASSWORD, "next": "/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return client


def test_previous_token_still_works_during_a_rotation(
    accounts: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESTO_API_TOKEN", _NEW)
    client = TestClient(create_app())
    assert client.get(_READ, headers=_bearer(_OLD)).status_code == 401

    monkeypatch.setenv("TESTO_API_TOKEN_PREVIOUS", _OLD)
    assert client.get(_READ, headers=_bearer(_OLD)).status_code == 200
    assert client.get(_READ, headers=_bearer(_NEW)).status_code == 200
    assert client.get(_READ, headers=_bearer("something-else")).status_code == 401


def test_token_login_accepts_the_previous_token(
    accounts: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESTO_API_TOKEN", _NEW)
    monkeypatch.setenv("TESTO_API_TOKEN_PREVIOUS", _OLD)
    client = TestClient(create_app())
    resp = client.post(
        "/api/v1/auth/login", data={"token": _OLD, "next": "/"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert client.get(_READ).status_code == 200


def test_without_a_session_key_a_new_token_signs_people_out(
    accounts: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _sign_in()
    assert client.get(_READ).status_code == 200
    monkeypatch.setenv("TESTO_API_TOKEN", _NEW)
    assert client.get(_READ).status_code == 401
    # ...unless the old token is still listed as the previous one.
    monkeypatch.setenv("TESTO_API_TOKEN_PREVIOUS", _OLD)
    assert client.get(_READ).status_code == 200


def test_session_key_keeps_people_signed_in_across_a_new_token(
    accounts: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESTO_SESSION_KEY", "a-separate-session-key")
    client = _sign_in()
    monkeypatch.setenv("TESTO_API_TOKEN", _NEW)
    assert client.get(_READ).status_code == 200
    # Changing the session key itself signs everyone out.
    monkeypatch.setenv("TESTO_SESSION_KEY", "another-key")
    assert client.get(_READ).status_code == 401
