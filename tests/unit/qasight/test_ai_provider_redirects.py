"""A provider that answers with a redirect never gets the API key resent elsewhere."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from qasight.services.ai import provider_base

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _serve(handler: type[BaseHTTPRequestHandler]) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_redirect_is_not_followed_with_the_key() -> None:
    seen: list[str | None] = []

    class Other(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return

    other = _serve(Other)

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{other.server_port}/steal")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return

    first = _serve(Redirect)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{first.server_port}/v1/chat/completions",
            data=b"{}",
            headers={"Authorization": "Bearer sk-secret"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as err:
            provider_base.urlopen(req, timeout=5)
        assert err.value.code == 302
        assert seen == []
    finally:
        first.shutdown()
        other.shutdown()
