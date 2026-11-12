"""Single sign-on with any OpenID Connect provider (Google, Microsoft Entra ID, Okta,
Keycloak, GitHub through Dex, ...), on top of ``TESTO_AUTH=accounts``.

Settings (all ``TESTO_OIDC_*``; SSO is on when the issuer, client id and secret are set):

* ``ISSUER``: the provider's issuer URL; ``<issuer>/.well-known/openid-configuration``
  must answer.
* ``CLIENT_ID`` / ``CLIENT_SECRET``: the app registered with the provider. Its redirect
  URL is ``https://<server>/api/v1/auth/oidc/callback`` (or ``REDIRECT_URL``).
* ``DEFAULT_ROLE``: role of an account the first sign-in creates (``viewer``).
* ``ALLOWED_DOMAINS``: comma-separated email domains allowed in (empty: any verified email).
* ``LABEL``: the button text on the sign-in page (``Sign in with SSO``).

The flow is the authorization-code flow with PKCE. The ID token is checked with the
provider's published keys (issuer, audience, expiry, nonce). The account name is the
verified email; an admin changes its role with ``qasight users set-role``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

OIDC_START_PATH = "/api/v1/auth/oidc/start"
OIDC_CALLBACK_PATH = "/api/v1/auth/oidc/callback"
STATE_COOKIE = "testo_oidc"
STATE_MAX_AGE = 600
_HTTP_TIMEOUT_S = 10.0


class OidcError(RuntimeError):
    """Sign-in with the provider failed; the message is safe to show."""


@dataclass(frozen=True)
class OidcSettings:
    issuer: str
    client_id: str
    client_secret: str
    redirect_url: str
    default_role: str
    allowed_domains: tuple[str, ...]
    label: str


def oidc_settings() -> OidcSettings | None:
    """The SSO settings, or None when SSO isn't set up."""
    issuer = os.getenv("TESTO_OIDC_ISSUER", "").strip().rstrip("/")
    client_id = os.getenv("TESTO_OIDC_CLIENT_ID", "").strip()
    secret = os.getenv("TESTO_OIDC_CLIENT_SECRET", "").strip()
    if not (issuer and client_id and secret):
        return None
    domains = tuple(
        d.strip().lower().lstrip("@")
        for d in os.getenv("TESTO_OIDC_ALLOWED_DOMAINS", "").split(",")
        if d.strip()
    )
    return OidcSettings(
        issuer=issuer,
        client_id=client_id,
        client_secret=secret,
        redirect_url=os.getenv("TESTO_OIDC_REDIRECT_URL", "").strip(),
        default_role=os.getenv("TESTO_OIDC_DEFAULT_ROLE", "viewer").strip() or "viewer",
        allowed_domains=domains,
        label=os.getenv("TESTO_OIDC_LABEL", "").strip() or "Sign in with SSO",
    )


@lru_cache(maxsize=4)
def _discovery_cached(issuer: str, _bucket: int) -> dict[str, Any]:
    import httpx

    url = f"{issuer}/.well-known/openid-configuration"
    try:
        response = httpx.get(url, timeout=_HTTP_TIMEOUT_S)
        response.raise_for_status()
        doc: dict[str, Any] = response.json()
    except Exception as exc:  # network, status or JSON: one message for the page
        raise OidcError(f"could not read the provider's settings at {url}") from exc
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if not isinstance(doc.get(key), str):
            raise OidcError(f"the provider's settings have no {key}")
    return doc


def discovery(issuer: str) -> dict[str, Any]:
    """The provider's discovery document, cached for an hour."""
    return _discovery_cached(issuer, int(time.time() // 3600))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _sign(key: str, payload: str) -> str:
    return hmac.new(key.encode(), b"testo-oidc-v1:" + payload.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class PendingSignIn:
    state: str
    verifier: str
    nonce: str
    next_path: str


def start(settings: OidcSettings, key: str, redirect_url: str, next_path: str) -> tuple[str, str]:
    """``(provider URL to send the browser to, value of the state cookie)``."""
    doc = discovery(settings.issuer)
    state, verifier, nonce = (secrets.token_urlsafe(24) for _ in range(3))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": redirect_url,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    expires = int(time.time()) + STATE_MAX_AGE
    payload = _b64(f"{state}\n{verifier}\n{nonce}\n{expires}\n{next_path}".encode())
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return doc["authorization_endpoint"] + sep + urlencode(
        params
    ), f"{payload}.{_sign(key, payload)}"


def read_state_cookie(key: str, cookie: str) -> PendingSignIn | None:
    """The sign-in this browser started, if the cookie is ours and not expired."""
    payload, _, sig = cookie.partition(".")
    if not payload or not hmac.compare_digest(sig.encode(), _sign(key, payload).encode()):
        return None
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        state, verifier, nonce, expires, next_path = raw.split("\n", 4)
    except (ValueError, UnicodeDecodeError):
        return None
    if int(expires) < time.time():
        return None
    return PendingSignIn(state, verifier, nonce, next_path)


def exchange_code(
    settings: OidcSettings, code: str, verifier: str, redirect_url: str
) -> dict[str, Any]:
    """Trade the code for tokens at the provider's token endpoint."""
    import httpx

    doc = discovery(settings.issuer)
    try:
        response = httpx.post(
            doc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_url,
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
            timeout=_HTTP_TIMEOUT_S,
        )
        response.raise_for_status()
        tokens: dict[str, Any] = response.json()
    except Exception as exc:
        raise OidcError("the provider refused the sign-in code") from exc
    if not isinstance(tokens.get("id_token"), str):
        raise OidcError("the provider sent no ID token")
    return tokens


@lru_cache(maxsize=4)
def _jwks_client(jwks_uri: str) -> Any:
    import jwt

    return jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)


def verify_id_token(settings: OidcSettings, id_token: str, nonce: str) -> dict[str, Any]:
    """The ID token's claims, after checking its signature, issuer, audience, expiry and nonce."""
    import jwt

    doc = discovery(settings.issuer)
    try:
        key = _jwks_client(doc["jwks_uri"]).get_signing_key_from_jwt(id_token).key
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
            audience=settings.client_id,
            issuer=doc["issuer"],
            leeway=60,
        )
    except Exception as exc:
        raise OidcError("the provider's ID token didn't check out") from exc
    if not hmac.compare_digest(str(claims.get("nonce", "")).encode(), nonce.encode()):
        raise OidcError("the provider's ID token is for another sign-in")
    return claims


def username_from_claims(settings: OidcSettings, claims: dict[str, Any]) -> str:
    """The account name for these claims: the verified email, in an allowed domain."""
    email = str(claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise OidcError("the provider didn't share an email address (ask for the email scope)")
    if claims.get("email_verified") is False:
        raise OidcError("that email address isn't verified with the provider")
    domain = email.rsplit("@", 1)[1]
    if settings.allowed_domains and domain not in settings.allowed_domains:
        raise OidcError(f"{domain} accounts can't sign in to this server")
    return email
