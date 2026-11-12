"""Contract for single sign-on (``TESTO_OIDC_*`` on top of ``TESTO_AUTH=accounts``).

The provider is faked: discovery and the token endpoint are patched, and the ID token is
signed with a key made here, so the real signature, audience, issuer and nonce checks run.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from qasight.accounts import AccountStore
from qasight.repository.db_config import reset_engine_cache
from qasight_api import oidc
from qasight_api.login_throttle import login_throttle
from qasight_api.main import create_app
from qasight_api.security import account_store

_ISSUER = "https://idp.example.com"
_CLIENT = "qasight-client"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Signing:
    key = _KEY.public_key()


class _Jwks:
    def get_signing_key_from_jwt(self, _token: str) -> _Signing:
        return _Signing()


@pytest.fixture
def sso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", "s3cret-token")
    monkeypatch.setenv("TESTO_AUTH", "accounts")
    monkeypatch.setenv("TESTO_OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("TESTO_OIDC_CLIENT_ID", _CLIENT)
    monkeypatch.setenv("TESTO_OIDC_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TESTO_OIDC_ALLOWED_DOMAINS", "example.com")
    monkeypatch.delenv("TESTO_OIDC_REDIRECT_URL", raising=False)
    monkeypatch.delenv("TESTO_OIDC_DEFAULT_ROLE", raising=False)
    reset_engine_cache()
    account_store.cache_clear()
    login_throttle.reset()
    doc = {
        "issuer": _ISSUER,
        "authorization_endpoint": f"{_ISSUER}/authorize",
        "token_endpoint": f"{_ISSUER}/token",
        "jwks_uri": f"{_ISSUER}/jwks",
    }
    monkeypatch.setattr(oidc, "discovery", lambda _issuer: doc)
    monkeypatch.setattr(oidc, "_jwks_client", lambda _uri: _Jwks())
    provider: dict[str, Any] = {"claims": {}, "key": _KEY, "seen": {}}

    def fake_exchange(_settings: Any, code: str, verifier: str, redirect_url: str) -> Any:
        provider["seen"] = {"code": code, "verifier": verifier, "redirect": redirect_url}
        now = int(time.time())
        claims = {"iss": _ISSUER, "aud": _CLIENT, "iat": now, "exp": now + 300}
        claims.update(provider["claims"])
        return {"id_token": jwt.encode(claims, provider["key"], algorithm="RS256")}

    monkeypatch.setattr("qasight_api.routes.auth.exchange_code", fake_exchange)
    yield provider
    account_store.cache_clear()
    reset_engine_cache()


def _start(client: TestClient) -> dict[str, str]:
    resp = client.get("/api/v1/auth/oidc/start?next=/runs", follow_redirects=False)
    assert resp.status_code == 303, resp.text
    location = resp.headers["location"]
    assert location.startswith(f"{_ISSUER}/authorize?")
    params = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
    assert params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == "http://testserver/api/v1/auth/oidc/callback"
    return params


def _sign_in(client: TestClient, provider: dict[str, Any], **claims: Any) -> Any:
    params = _start(client)
    provider["claims"] = {"nonce": params["nonce"], **claims}
    return client.get(
        f"/api/v1/auth/oidc/callback?code=abc&state={params['state']}", follow_redirects=False
    )


def test_login_page_offers_sso(sso: dict[str, Any]) -> None:
    page = TestClient(create_app()).get("/login?next=/runs").text
    assert "Sign in with SSO" in page and "/api/v1/auth/oidc/start?next=%2Fruns" in page


def test_first_sign_in_creates_a_viewer_and_signs_in(sso: dict[str, Any]) -> None:
    client = TestClient(create_app())
    resp = _sign_in(client, sso, email="Dana@Example.com", email_verified=True)
    assert resp.status_code == 200, resp.text
    assert 'content="0;url=/runs"' in resp.text
    assert sso["seen"]["code"] == "abc" and sso["seen"]["verifier"]
    account = account_store().get("dana@example.com")
    assert account is not None and account.role == "viewer"
    me = client.get("/api/v1/auth/me").json()
    assert me == {
        "auth_mode": "accounts",
        "signed_in": True,
        "username": "dana@example.com",
        "role": "viewer",
    }
    assert client.get("/api/v1/runs").status_code == 200


def test_existing_account_keeps_its_role(sso: dict[str, Any]) -> None:
    store: AccountStore = account_store()
    store.add("ada@example.com", "correct horse battery", "admin")
    client = TestClient(create_app())
    assert _sign_in(client, sso, email="ada@example.com").status_code == 200
    assert client.get("/api/v1/auth/me").json()["role"] == "admin"


@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ({"email": "eve@elsewhere.org"}, "elsewhere.org accounts can&#x27;t sign in"),
        ({"email": "dana@example.com", "email_verified": False}, "isn&#x27;t verified"),
        ({}, "didn&#x27;t share an email"),
    ],
)
def test_refuses_other_domains_unverified_and_missing_email(
    sso: dict[str, Any], claims: dict[str, Any], message: str
) -> None:
    client = TestClient(create_app())
    resp = _sign_in(client, sso, **claims)
    assert resp.status_code == 401
    assert message in resp.text
    assert client.get("/api/v1/runs").status_code == 401


def test_refuses_a_token_signed_by_another_key(sso: dict[str, Any]) -> None:
    sso["key"] = _OTHER_KEY
    resp = _sign_in(TestClient(create_app()), sso, email="dana@example.com")
    assert resp.status_code == 401 and "didn&#x27;t check out" in resp.text


def test_refuses_a_wrong_nonce(sso: dict[str, Any]) -> None:
    client = TestClient(create_app())
    params = _start(client)
    sso["claims"] = {"nonce": "someone-elses", "email": "dana@example.com"}
    resp = client.get(f"/api/v1/auth/oidc/callback?code=abc&state={params['state']}")
    assert resp.status_code == 401 and "another sign-in" in resp.text


def test_refuses_a_callback_without_the_state_cookie(sso: dict[str, Any]) -> None:
    params = _start(TestClient(create_app()))
    fresh = TestClient(create_app())
    resp = fresh.get(f"/api/v1/auth/oidc/callback?code=abc&state={params['state']}")
    assert resp.status_code == 400 and "expired" in resp.text


def test_sso_is_off_without_accounts_mode(
    sso: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESTO_AUTH", "token")
    client = TestClient(create_app())
    assert "Sign in with SSO" not in client.get("/login").text
    assert client.get("/api/v1/auth/oidc/start", follow_redirects=False).status_code == 404
