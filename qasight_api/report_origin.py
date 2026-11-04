"""Serve run reports from their own origin (``TESTO_REPORTS_ORIGIN``, off by default).

Allure and native report pages run script. Served from the API's own origin, a
script in a report would act with the viewer's session. With
``TESTO_REPORTS_ORIGIN=https://reports.testo.example.com`` (a second hostname
pointed at the same server):

- The API host no longer serves ``/history``; it redirects there, after checking
  the viewer may read, with a short-lived signed ticket.
- The reports host serves only ``/history`` and the ticket exchange. It trades the
  ticket for its own HttpOnly cookie, which is good for nothing but reading
  reports on that host. Its origin is not the API's, so the API's Origin check
  refuses any write a report page tries.

Tickets and cookies are signed with ``TESTO_SESSION_KEY``, or ``TESTO_API_TOKEN``
when that isn't set (see :func:`qasight_api.security.signing_key`). Without a token, reads
are open anyway, so the redirect carries no ticket.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode, urlsplit

from fastapi import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from qasight_api.security import (
    account_store,
    accounts_enabled,
    api_token,
    current_principal,
    reads_protected,
    safe_next,
    session_tag,
    signing_key,
    verification_keys,
)

REPORTS_ORIGIN_ENV = "TESTO_REPORTS_ORIGIN"
REPORTS_COOKIE = "testo_reports"
TICKET_PATH = "/_report-auth"
TICKET_MAX_AGE_S = 60
REPORTS_COOKIE_MAX_AGE_S = 12 * 3600


def reports_origin() -> str | None:
    raw = os.environ.get(REPORTS_ORIGIN_ENV, "").strip().rstrip("/")
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _sign(token: str, kind: str, payload: str) -> str:
    sig = hmac.new(token.encode(), f"testo-{kind}-v1:{payload}".encode(), hashlib.sha256)
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}.{sig.hexdigest()}"


def _unsign(keys: list[str], kind: str, value: str) -> list[str] | None:
    """The ``:``-separated fields of a value signed by `_sign` with one of ``keys``,
    if valid and unexpired."""
    encoded, _, sig = value.partition(".")
    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    signed = [
        hmac.compare_digest(
            sig.encode(),
            hmac.new(key.encode(), f"testo-{kind}-v1:{payload}".encode(), hashlib.sha256)
            .hexdigest()
            .encode(),
        )
        for key in keys
    ]
    if not any(signed):
        return None
    fields = payload.split(":")
    try:
        if int(fields[-1]) < time.time():
            return None
    except ValueError:
        return None
    return fields


def _session_version(username: str) -> str:
    """What ties a reports cookie to the viewer's session: an account's version, or 0."""
    if username == "token" or not accounts_enabled():
        return "0"
    account = account_store().get(username)
    return "" if account is None else session_tag(account)


def _still_valid(username: str, version: str) -> bool:
    current = _session_version(username)
    return bool(current) and current == version


def route_report_request(request: Request) -> Response | None:
    """The response for a request the reports split decides, or None to carry on.

    Sets ``request.state.report_reader`` when the reports host lets a read through,
    so the API's own read guard (which only knows the API host's cookie) skips it.
    """
    origin = reports_origin()
    if origin is None:
        return None
    path = request.url.path
    on_reports_host = request.headers.get("host", "") == urlsplit(origin).netloc
    token = signing_key() if api_token() else ""

    if on_reports_host:
        if path == TICKET_PATH:
            return _exchange_ticket(request, token)
        if not path.startswith("/history/") or request.method not in ("GET", "HEAD"):
            return PlainTextResponse("Not found", status_code=404)
        if reads_protected():
            fields = _unsign(
                verification_keys(), "reports", request.cookies.get(REPORTS_COOKIE, "")
            )
            if fields is None or len(fields) != 3 or not _still_valid(fields[0], fields[1]):
                return PlainTextResponse(
                    "Open this report from the QASight UI to sign in.", status_code=401
                )
        request.state.report_reader = True
        return None

    if path.startswith("/history/") and request.method in ("GET", "HEAD"):
        target = path + (f"?{request.url.query}" if request.url.query else "")
        if not reads_protected():
            return RedirectResponse(origin + target, status_code=303)
        principal = current_principal(request)
        if principal is None:
            return None  # the read guard sends the browser to /login first
        expires = int(time.time()) + TICKET_MAX_AGE_S
        ticket = _sign(
            token,
            "ticket",
            f"{principal.username}:{_session_version(principal.username)}:{expires}",
        )
        query = urlencode({"ticket": ticket, "next": target})
        return RedirectResponse(f"{origin}{TICKET_PATH}?{query}", status_code=303)
    return None


def _exchange_ticket(request: Request, token: str) -> Response:
    target = safe_next(request.query_params.get("next"))
    if not target.startswith("/history/"):
        target = "/history/"
    if not token:
        return RedirectResponse(target, status_code=303)
    fields = _unsign(verification_keys(), "ticket", request.query_params.get("ticket", ""))
    if fields is None or len(fields) != 3 or not _still_valid(fields[0], fields[1]):
        return PlainTextResponse(
            "This report link has expired. Open it again from the QASight UI.",
            status_code=401,
        )
    expires = int(time.time()) + REPORTS_COOKIE_MAX_AGE_S
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        REPORTS_COOKIE,
        _sign(token, "reports", f"{fields[0]}:{fields[1]}:{expires}"),
        max_age=REPORTS_COOKIE_MAX_AGE_S,
        httponly=True,
        # Lax: the cookie is set at the end of a redirect chain that started on the
        # API's host, which may be a different site.
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response
