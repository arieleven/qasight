"""Minimal access control for a local developer tool.

The API starts test runs, i.e. it runs commands on the host by design. It binds
127.0.0.1 by default; these knobs keep that honest when it is not:

- ``TESTO_API_TOKEN``: when set, every mutating request needs
  ``Authorization: Bearer <token>`` or the session cookie that ``/login``
  hands out for that token. Reads stay open unless:
- ``TESTO_PROTECT_READS=1`` (with a token): every request needs the token or
  the cookie, including the UI, report files under ``/history`` and the live
  run stream, which a browser can't send a header with. Health checks and
  the login page stay open. See :func:`guard_read_request`.
- ``TESTO_AUTH=accounts`` (with a token): people sign in with their own
  username and password (:mod:`testo_core.accounts`), reads are protected, writes
  need the ``runner`` role and server settings ``admin``. The token stays an
  admin credential for CI and signs the session cookies (or ``TESTO_SESSION_KEY``
  does, when set). Default ``token``.
- ``TESTO_API_TOKEN_PREVIOUS``: an old token still accepted while CI secrets are
  moved to a new ``TESTO_API_TOKEN``; remove it when the rotation is done.
- ``TESTO_SESSION_KEY``: signs account cookies and report tickets instead of the
  token, so rotating the token doesn't sign people out. See :func:`signing_key`.
- ``TESTO_CORS_ORIGINS``: comma-separated browser origins allowed to call the
  API. Defaults to the Vite dev and preview servers. A mutating request that
  carries any other ``Origin`` is refused even without a token, because CORS
  alone does not stop a "simple" cross-site POST (or a DNS-rebinding page)
  from reaching a localhost server. ``*`` waives that check only with a token.
- Without a token, a request whose ``Host`` is not a loopback name (or listed in
  ``TESTO_ALLOWED_HOSTS``) is refused, so a DNS-rebinding page can't read the
  API either (see :func:`guard_host`). ``TESTO_ALLOW_UNAUTHENTICATED=1`` lifts it. With a token set, the UI served by this
  process (same origin, see :mod:`testo_api.ui`) may write too; the token
  still has to be sent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, RedirectResponse, Response

if TYPE_CHECKING:
    from testo_core.accounts import Account, AccountStore

logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# File names config discovery accepts (``config/loader.py``); see ``read_config_path``.
CONFIG_FILE_NAMES = frozenset({"testosterone.yaml", "testosterone.yml", "pyproject.toml"})
SESSION_COOKIE = "testo_session"
LOGIN_PATH = "/login"
LOGIN_POST_PATH = "/api/v1/auth/login"
LOGOUT_PATH = "/api/v1/auth/logout"
# Reachable without a session even when reads are protected.
_OPEN_PATHS = frozenset({LOGIN_PATH, LOGIN_POST_PATH, LOGOUT_PATH})
_OPEN_PREFIXES = ("/api/v1/health/",)
# Writes under these need the admin role in accounts mode; every other write needs runner.
# Taking a key back from its holder is an admin's call; taking and giving back your own is a runner's.
# Deleting run history can't be undone, so it is an admin's call too.
_ADMIN_WRITE_PREFIXES = ("/api/v1/ai/", "/api/v1/resources/items/release", "/api/v1/runs:delete")
SESSION_MAX_AGE = 30 * 24 * 3600

AuthMode = Literal["token", "accounts"]


@dataclass(frozen=True)
class Principal:
    """Who a request comes from: an account, or ``token`` for the API token (an admin)."""

    username: str
    role: str


TOKEN_PRINCIPAL = Principal(username="token", role="admin")


def api_token() -> str:
    return os.getenv("TESTO_API_TOKEN", "").strip()


def previous_api_token() -> str:
    """``TESTO_API_TOKEN_PREVIOUS``: still accepted during a rotation (only with a token set)."""
    previous = os.getenv("TESTO_API_TOKEN_PREVIOUS", "").strip()
    return previous if api_token() and previous != api_token() else ""


def accepted_tokens() -> list[str]:
    """The tokens a bearer header or the token login may carry: current first, then previous."""
    return [t for t in (api_token(), previous_api_token()) if t]


def token_matches(supplied: str) -> bool:
    """``supplied`` is the current token or, during a rotation, the previous one."""
    value = supplied.strip().encode()
    # Compare against every accepted token, so the time taken doesn't say which matched.
    results = [hmac.compare_digest(value, t.encode()) for t in accepted_tokens()]
    return any(results)


def signing_key() -> str:
    """The key account cookies and report tickets are signed with.

    ``TESTO_SESSION_KEY`` when set, so a new ``TESTO_API_TOKEN`` (someone left, a CI
    secret leaked) keeps people signed in; otherwise the token, as before.
    """
    return os.getenv("TESTO_SESSION_KEY", "").strip() or api_token()


def verification_keys() -> list[str]:
    """Keys a signed cookie or ticket may carry: :func:`signing_key`, plus the previous
    token while one is set and no separate session key is, so a rotation signs nobody out."""
    keys = [signing_key()]
    previous = previous_api_token()
    if previous and not os.getenv("TESTO_SESSION_KEY", "").strip():
        keys.append(previous)
    return [k for k in keys if k]


def auth_mode() -> AuthMode:
    raw = os.getenv("TESTO_AUTH", "token").strip().lower() or "token"
    return "accounts" if raw == "accounts" else "token"


def accounts_enabled() -> bool:
    """``TESTO_AUTH=accounts`` and a token to sign sessions with."""
    return auth_mode() == "accounts" and bool(api_token())


def reads_protected() -> bool:
    """Reads need a session: ``TESTO_PROTECT_READS`` is on, or accounts are (needs a token)."""
    flag = os.getenv("TESTO_PROTECT_READS", "0").strip().lower() in ("1", "true", "yes")
    return (flag or auth_mode() == "accounts") and bool(api_token())


@cache
def account_store() -> AccountStore:
    """The accounts on the run-history database, opened on first use."""
    from testo_core.accounts import AccountStore

    return AccountStore()


def session_tag(account: Account) -> str:
    """What ties a cookie to one account: its session version and when it was created.

    The creation time means removing a user and adding the name again doesn't bring
    the old person's cookies back, though the new row starts at version 1 again.
    """
    created = int(account.created_at.timestamp() * 1_000_000)
    return f"{account.session_version}.{created}"


def account_session_value(token: str, account: Account, *, now: float | None = None) -> str:
    """Cookie for an account: ``<user>:<session version>:<expiry>`` signed with the token.

    Changing the token, or bumping the account's session version (role or password
    change, ``testo users sign-out``), invalidates it.
    """
    expires = int((time.time() if now is None else now) + SESSION_MAX_AGE)
    payload = f"{account.username}:{session_tag(account)}:{expires}".encode()
    sig = hmac.new(token.encode(), b"testo-account-v1:" + payload, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + sig


def _account_from_cookie(keys: list[str], cookie: str) -> Principal | None:
    encoded, _, sig = cookie.partition(".")
    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        username, version, expires = payload.decode().split(":")
    except (ValueError, UnicodeDecodeError):
        return None
    signed = [
        hmac.compare_digest(
            sig.encode(),
            hmac.new(key.encode(), b"testo-account-v1:" + payload, hashlib.sha256)
            .hexdigest()
            .encode(),
        )
        for key in keys
    ]
    if not any(signed):
        return None
    if int(expires) < time.time():
        return None
    account = account_store().get(username)
    if account is None or session_tag(account) != version:
        return None
    return Principal(username=account.username, role=account.role)


def session_value(token: str) -> str:
    """The cookie value for ``token``: derived from it, so the cookie never holds
    the token itself, and changing the token signs everyone out."""
    return hmac.new(token.encode(), b"testo-session-v1", hashlib.sha256).hexdigest()


def current_principal(request: Request) -> Principal | None:
    """Who sent ``request``: a valid bearer token or session cookie, else None.

    Always None without ``TESTO_API_TOKEN``. Worked out once per request.
    """
    if "principal" in request.scope.get("state", {}):
        return request.state.principal  # type: ignore[no-any-return]
    principal = _resolve_principal(request)
    request.state.principal = principal
    return principal


def _resolve_principal(request: Request) -> Principal | None:
    if not api_token():
        return None
    scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token_matches(supplied):
        return TOKEN_PRINCIPAL
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if not cookie:
        return None
    token_cookies = [
        hmac.compare_digest(cookie.encode(), session_value(t).encode()) for t in accepted_tokens()
    ]
    if any(token_cookies):
        return TOKEN_PRINCIPAL
    if not accounts_enabled():
        return None
    try:
        return _account_from_cookie(verification_keys(), cookie)
    except Exception:
        # Broad on purpose: a database error means nobody can be verified, not a 500 page.
        logger.warning("could not check the session against the accounts", exc_info=True)
        return None


def is_authenticated(request: Request) -> bool:
    """A valid bearer token or session cookie (always False without a token)."""
    return current_principal(request) is not None


def audit_write(request: Request, status: int) -> None:
    """Record a write in the audit log (accounts mode); the login route records sign-ins."""
    principal = request.scope.get("state", {}).get("principal")
    if principal is None or request.url.path == LOGIN_POST_PATH:
        return
    try:
        account_store().record(
            username=principal.username,
            method=request.method,
            path=request.url.path,
            status=status,
        )
    except Exception:
        # Broad on purpose: the write already happened; a lost audit line is only logged.
        logger.warning("could not write the audit log", exc_info=True)


def required_role(request: Request) -> str:
    """The role a write needs in accounts mode."""
    return "admin" if request.url.path.startswith(_ADMIN_WRITE_PREFIXES) else "runner"


def safe_next(raw: str | None) -> str:
    """Where to go after login: a path on this server only, never another host."""
    if not raw or not raw.startswith("/") or raw.startswith(("//", "/\\")):
        return "/"
    return raw


def cors_settings() -> tuple[list[str], bool]:
    """Return ``(allowed_origins, allow_credentials)`` for ``CORSMiddleware``.

    Credentials are only allowed for an explicit origin list: browsers reject
    them with ``*``. The UI that uses the session cookie is served by the API
    itself (same origin), so it needs no CORS credentials either.
    """
    raw = os.getenv("TESTO_CORS_ORIGINS", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if not origins:
        return list(DEFAULT_CORS_ORIGINS), False
    return origins, "*" not in origins


def guard_mutating_request(request: Request) -> None:
    """App-wide dependency: refuse foreign-origin writes and enforce ``TESTO_API_TOKEN``."""
    if request.method in _SAFE_METHODS:
        return
    origin = request.headers.get("Origin")
    allowed_origins, _ = cors_settings()
    expected = api_token()
    # ``*`` only waives the check when a token is required anyway: without one it
    # would let any web page start runs (run commands) on this machine.
    wildcard = "*" in allowed_origins and bool(expected)
    if (
        origin is not None
        and not wildcard
        and origin not in allowed_origins
        and not (expected and _is_same_origin(request, origin))
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Origin {origin!r} is not allowed (set TESTO_CORS_ORIGINS).",
        )
    # The login form is how a browser gets a session, so it can't need one.
    if not expected or request.url.path == LOGIN_POST_PATH:
        return
    principal = current_principal(request)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if accounts_enabled() and request.url.path != LOGOUT_PATH:
        from testo_core.accounts import role_at_least

        needed = required_role(request)
        if not role_at_least(principal.role, needed):
            raise HTTPException(
                status_code=403,
                detail=f"This needs the {needed} role; {principal.username} is a "
                f"{principal.role}. Ask an admin (`testo users set-role`).",
            )


def unauthenticated_allowed() -> bool:
    """``TESTO_ALLOW_UNAUTHENTICATED=1``: a token-less API on a network the operator trusts."""
    return os.getenv("TESTO_ALLOW_UNAUTHENTICATED", "0").strip().lower() in ("1", "true", "yes")


def _allowed_hosts() -> set[str]:
    extra = os.getenv("TESTO_ALLOWED_HOSTS", "")
    hosts = {h.strip().lower() for h in extra.split(",") if h.strip()}
    reports = os.getenv("TESTO_REPORTS_ORIGIN", "").strip()
    if reports:
        hostname = urlsplit(reports).hostname
        if hostname:
            hosts.add(hostname.lower())
    return hosts


def guard_host(request: Request) -> Response | None:
    """Without a token, only answer requests addressed to this machine by a loopback name.

    A token-less API binds 127.0.0.1, but a web page can still reach it through DNS
    rebinding: its own hostname is re-pointed at 127.0.0.1, so the browser treats the
    API as that page's origin and lets it read every GET response (runs, logs,
    reports). Such a request still names the page's hostname in ``Host``, so it is
    refused here. Extra names go in ``TESTO_ALLOWED_HOSTS``; with a token, or
    ``TESTO_ALLOW_UNAUTHENTICATED=1``, any ``Host`` is answered.
    """
    if api_token() or unauthenticated_allowed():
        return None
    host = request.headers.get("Host", "")
    hostname = urlsplit(f"//{host}").hostname or ""
    if is_loopback_host(hostname) or hostname in _allowed_hosts():
        return None
    return JSONResponse(
        status_code=421,
        content={
            "error": {
                "code": "misdirected_request",
                "message": (
                    f"Host {host!r} is not a loopback name. Without TESTO_API_TOKEN this API "
                    "only answers localhost; add the name to TESTO_ALLOWED_HOSTS."
                ),
                "details": None,
            },
            "request_id": getattr(request.state, "request_id", None),
        },
    )


def guard_read_request(request: Request) -> Response | None:
    """With ``TESTO_PROTECT_READS``: the response that turns away a request
    without a session, or None to let it through.

    Runs as middleware rather than a route dependency so it also covers the
    ``/history`` report files and the UI, which are static mounts. A browser
    asking for a page is sent to ``/login``; anything else gets the API's 401.
    """
    if not reads_protected() or request.method == "OPTIONS":
        return None
    if request.scope.get("state", {}).get("report_reader"):
        return None  # the reports host checked its own cookie (`report_origin`)
    path = request.url.path
    if path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES) or is_authenticated(request):
        return None
    wants_page = request.method in ("GET", "HEAD") and "text/html" in request.headers.get(
        "Accept", ""
    )
    if wants_page and not path.startswith("/api/"):
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"{LOGIN_PATH}?{urlencode({'next': target})}", status_code=303)
    return JSONResponse(
        status_code=401,
        content={
            "error": {
                "code": "unauthorized",
                "message": f"Sign in at {LOGIN_PATH} or send the API token.",
                "details": None,
            },
            "request_id": getattr(request.state, "request_id", None),
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


# Raw result folders hold data and attachments only, never a page meant to run, so
# everything under one is sandboxed whatever its name.
_ATTACHMENT_MARKERS = ("-attachment", "/data/attachments/", "/allure-results/")


def add_security_headers(request: Request, response: Response) -> None:
    """Headers every response gets, plus a sandbox for report attachments.

    Report files under ``/history`` share the API's origin, so an uploaded HTML
    attachment would otherwise run script with the viewer's session. Allure names
    attachments ``<uuid>-attachment.<ext>`` in results and copies them to
    ``data/attachments/`` in a report; those get ``sandbox``, which blocks script
    while still letting the report show the file. The report pages themselves keep
    working, since they need script and their own origin to load their data.
    """
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    path = request.url.path
    if path.startswith("/history/") and any(m in path for m in _ATTACHMENT_MARKERS):
        response.headers["Content-Security-Policy"] = "sandbox; frame-ancestors 'self'"
    else:
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")


def read_config_path(raw: str | None) -> Path | None:
    """``?config_path=`` on a read route, limited to config file names.

    Reads need no token, so an arbitrary path would let anyone who can reach the
    API probe for files and see fragments of them in parse errors. Only a file
    named like a config (``testosterone.yaml``, ``testosterone.yml``,
    ``pyproject.toml``) is loaded; writes, which need the token, take any path.
    """
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if path.name not in CONFIG_FILE_NAMES:
        raise HTTPException(
            status_code=400,
            detail="config_path must name a testosterone.yaml, testosterone.yml or pyproject.toml file.",
        )
    return path


def _is_same_origin(request: Request, origin: str) -> bool:
    """The UI served by this process (``TESTO_UI_DIR``) posting back to it.

    Only trusted together with a token: a DNS-rebinding page is "same origin"
    too, so without ``TESTO_API_TOKEN`` the Origin allow-list stays the only guard.
    """
    host = request.headers.get("Host", "")
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


def is_loopback_host(host: str) -> bool:
    host = host.strip("[]").rstrip(".")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
