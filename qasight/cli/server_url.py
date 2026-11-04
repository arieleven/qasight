"""Check a ``--server`` URL before using it (P16 in docs/engine-cli-review.md).

``qasight ingest --server not-a-url`` used to try to connect and exit 3 ("could not
reach"), as if the server were down. A URL without ``http://`` or ``https://``
is a typo, so it is a usage error (exit 2) that says what is wrong.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def server_url_problem(url: str | None, *, flag: str = "--server") -> str | None:
    """Why *url* can't be a server address, or None (also for no URL at all)."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return (
            f"{flag} must be a URL like https://testo.example.com (or $TESTO_SERVER_URL); "
            f"got {url!r}"
        )
    return None
