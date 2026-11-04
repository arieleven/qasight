"""Browser sign-in for a team server: trade the API token, or with
``TESTO_AUTH=accounts`` a username and password, for a session cookie.

Report links open in a new tab and the live run stream uses ``EventSource``;
neither can send an ``Authorization`` header, so a browser proves itself with
an HttpOnly cookie instead (see :mod:`qasight_api.security`). The page is plain
HTML from the API so it works before, and without, the React UI.
"""

from __future__ import annotations

import logging
from datetime import datetime
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from qasight_api.login_throttle import login_throttle
from qasight_api.security import (
    LOGIN_PATH,
    LOGIN_POST_PATH,
    LOGOUT_PATH,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    Principal,
    account_session_value,
    account_store,
    accounts_enabled,
    api_token,
    auth_mode,
    current_principal,
    safe_next,
    session_value,
    signing_key,
    token_matches,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · QASight</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f1115; color: #e6e8eb;
         display: grid; place-items: center; min-height: 100vh; margin: 0; }}
  form {{ background: #181b21; padding: 2rem; border-radius: 12px; width: min(22rem, 90vw); }}
  h1 {{ font-size: 1.2rem; margin: 0 0 1rem; }}
  label {{ display: block; font-size: .85rem; margin: .8rem 0 .4rem; color: #a3a9b3; }}
  p.hint {{ font-size: .75rem; color: #a3a9b3; }}
  input {{ width: 100%; box-sizing: border-box; padding: .6rem; border-radius: 8px;
          border: 1px solid #333944; background: #0f1115; color: inherit; }}
  button {{ margin-top: 1rem; width: 100%; padding: .6rem; border: 0; border-radius: 8px;
           background: #e2553f; color: white; font-weight: 600; cursor: pointer; }}
  p.error {{ color: #ff8a7a; font-size: .85rem; }}
</style>
</head>
<body>
<form method="post" action="{action}">
  <h1>Sign in to QASight</h1>
  {error}
  {fields}
  <input type="hidden" name="next" value="{next}">
  <button type="submit">Sign in</button>
</form>
</body>
</html>
"""


_TOKEN_FIELDS = """<label for="token">Team server token (<code>TESTO_API_TOKEN</code>)</label>
  <input id="token" name="token" type="password" autocomplete="current-password" required autofocus>"""

_ACCOUNT_FIELDS = """<label for="username">Username</label>
  <input id="username" name="username" autocomplete="username" autofocus>
  <label for="token">Password</label>
  <input id="token" name="token" type="password" autocomplete="current-password" required>
  <p class="hint">Admins can leave the username empty and use the server token.</p>"""


def _page(next_path: str, error: str = "", status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(
            action=LOGIN_POST_PATH,
            next=escape(next_path, quote=True),
            fields=_ACCOUNT_FIELDS if accounts_enabled() else _TOKEN_FIELDS,
            error=f'<p class="error">{escape(error)}</p>' if error else "",
        ),
        status_code=status_code,
    )


@router.get(LOGIN_PATH, include_in_schema=False)
def login_page(next: str | None = None) -> HTMLResponse:  # noqa: A002 - query name
    return _page(safe_next(next))


def _audit(username: str, request: Request, status: int) -> None:
    try:
        account_store().record(
            username=username, method=request.method, path=request.url.path, status=status
        )
    except Exception:
        # Broad on purpose: a failed audit write must not block signing in.
        logger.warning("could not write the audit log", exc_info=True)


@router.post(LOGIN_POST_PATH, include_in_schema=False)
def login(
    request: Request,
    token: str = Form(...),
    username: str = Form(""),
    next: str = Form("/"),  # noqa: A002 - form field name
) -> Response:
    expected = api_token()
    target = safe_next(next)
    if not expected:
        return _page(
            target, "This server has no TESTO_API_TOKEN, so there is nothing to sign in to.", 400
        )
    accounts = accounts_enabled()
    name = username.strip()[:64] if accounts and username.strip() else "token"
    address = request.client.host if request.client else "unknown"
    if accounts:
        wait = login_throttle.retry_after(name, address)
        if wait:
            _audit(name, request, 429)
            refusal = _page(
                target, f"Too many failed sign-ins. Try again in {wait // 60 + 1} minutes.", 429
            )
            refusal.headers["Retry-After"] = str(wait)
            return refusal
    if accounts and name != "token":
        account = account_store().authenticate(username, token)
        if account is None:
            login_throttle.failed(name, address)
            _audit(name, request, 401)
            return _page(target, "That username and password don't match.", 401)
        login_throttle.succeeded(name)
        _audit(account.username, request, 303)
        cookie = account_session_value(signing_key(), account)
    elif token_matches(token):
        if accounts:
            login_throttle.succeeded(name)
            _audit("token", request, 303)
        cookie = session_value(expected)
    else:
        if accounts:
            login_throttle.failed(name, address)
            _audit("token", request, 401)
        return _page(target, "That token is not right.", 401)
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        cookie,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        # Behind an HTTPS proxy, set FORWARDED_ALLOW_IPS to the proxy's address so
        # uvicorn trusts its X-Forwarded-Proto and this sees "https".
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@router.post(LOGOUT_PATH, include_in_schema=False)
def logout() -> Response:
    response = RedirectResponse(LOGIN_PATH, status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


class WhoAmIResponse(BaseModel):
    auth_mode: str
    signed_in: bool
    username: str | None = None
    role: str | None = None


@router.get("/api/v1/auth/me", response_model=WhoAmIResponse)
async def who_am_i(request: Request) -> WhoAmIResponse:
    """Who this browser or token is signed in as (``auth_mode`` is ``token`` or ``accounts``)."""
    principal = await run_in_threadpool(current_principal, request)
    return WhoAmIResponse(
        auth_mode=auth_mode(),
        signed_in=principal is not None,
        username=principal.username if principal else None,
        role=principal.role if principal else None,
    )


class AuditEntryResponse(BaseModel):
    at: datetime
    username: str
    method: str
    path: str
    status: int


def _require_admin(request: Request) -> Principal:
    if not accounts_enabled():
        raise HTTPException(status_code=404, detail="The audit log needs TESTO_AUTH=accounts.")
    principal = current_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Sign in first.")
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="The audit log is for admins.")
    return principal


@router.get("/api/v1/audit", response_model=list[AuditEntryResponse])
def audit_log(
    limit: int = Query(100, ge=1, le=1000),
    _admin: Principal = Depends(_require_admin),
) -> list[AuditEntryResponse]:
    """Recent writes and sign-ins, newest first (admins, ``TESTO_AUTH=accounts`` only)."""
    return [
        AuditEntryResponse(
            at=e.at, username=e.username, method=e.method, path=e.path, status=e.status
        )
        for e in account_store().recent(limit)
    ]
