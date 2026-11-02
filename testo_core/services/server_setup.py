"""``testo server init``: the files to run a team server with Docker Compose.

Writes, into a folder of its own:

- ``docker-compose.yml``: the same file as ``deploy/server/docker-compose.yml``
  in the repository (a copy ships in the wheel; a test keeps them equal),
  without its ``build:`` sections, so it pulls the published image.
- ``.env`` (mode 600): a generated ``TESTO_API_TOKEN`` and Postgres password,
  and with accounts, ``TESTO_AUTH=accounts`` and a first admin
  (``TESTO_ADMIN_USER`` / ``TESTO_ADMIN_PASSWORD``) the server creates when it
  starts with no accounts (:func:`testo_core.accounts.ensure_first_admin`).
- With a domain: a ``Caddyfile`` and ``docker-compose.override.yml`` adding
  Caddy, which gets a certificate for the domain by itself; the server's own
  port is then published on 127.0.0.1 only.

Secrets are only written to ``.env``, never printed.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "server" / "docker-compose.yml"
_DOMAIN = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)
_BUILD = re.compile(r"^    build:\n(?:      .*\n)+", re.MULTILINE)


class ServerSetupError(ValueError):
    """A bad domain or admin name, or files that exist (without ``--force``)."""


@dataclass(frozen=True)
class ServerFiles:
    folder: Path
    written: list[str]
    url: str
    admin: str | None


def compose_yaml() -> str:
    """The team server compose file, pulling the published image (no ``build:``)."""
    text = TEMPLATE.read_text(encoding="utf-8")
    text = _BUILD.sub("", text)
    return text.replace(
        "docker compose up -d                                # from a clone: add --build",
        "docker compose up -d",
    )


def caddyfile(domain: str) -> str:
    return (
        "# Written by `testo server init --domain`. Caddy gets and renews the certificate;\n"
        "# ports 80 and 443 must reach this machine and the domain's DNS must point here.\n"
        f"{domain} {{\n"
        "\treverse_proxy server:8000\n"
        "}\n"
    )


def override_yaml() -> str:
    return (
        "# Written by `testo server init --domain`: HTTPS in front of the server.\n"
        "services:\n"
        "  server:\n"
        "    environment:\n"
        "      # Only Caddy reaches the server (its port is on 127.0.0.1), so trust its\n"
        "      # X-Forwarded-Proto: sign-in cookies are then marked Secure.\n"
        '      FORWARDED_ALLOW_IPS: "*"\n'
        "  caddy:\n"
        "    image: caddy:2\n"
        "    ports:\n"
        '      - "80:80"\n'
        '      - "443:443"\n'
        "    volumes:\n"
        "      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n"
        "      - caddy_data:/data\n"
        "      - caddy_config:/config\n"
        "    depends_on:\n"
        "      - server\n"
        "    restart: unless-stopped\n"
        "volumes:\n"
        "  caddy_data:\n"
        "  caddy_config:\n"
    )


def env_file(
    *,
    domain: str | None,
    admin: str | None,
    token: str,
    db_password: str,
    admin_password: str,
    session_key: str = "",
) -> str:
    lines = [
        "# Written by `testo server init`. Secrets: keep this file private (mode 600).",
        "# CI uploads with TESTO_API_TOKEN (add it as a CI secret). To rotate it, move the",
        "# old value to TESTO_API_TOKEN_PREVIOUS until every CI secret has the new one.",
        f"TESTO_API_TOKEN={token}",
        f"POSTGRES_PASSWORD={db_password}",
    ]
    if session_key:
        lines += [
            "# Signs sign-in cookies, so a new TESTO_API_TOKEN doesn't sign people out.",
            f"TESTO_SESSION_KEY={session_key}",
        ]
    lines += [
        "# Runs (and report archives) older than this many days are deleted; 0 keeps all.",
        "TESTO_RETENTION_DAYS=180",
        "# Files bigger than this (videos, traces) aren't kept in a run's history copy.",
        "TESTO_HISTORY_MAX_FILE_MB=100",
    ]
    if admin:
        lines += [
            "# People sign in with their own accounts; this admin is created on first start.",
            "TESTO_AUTH=accounts",
            f"TESTO_ADMIN_USER={admin}",
            f"TESTO_ADMIN_PASSWORD={admin_password}",
        ]
    if domain:
        lines += [
            "# Caddy serves https://" + domain + "; the server's own port stays on this machine.",
            "TESTO_SERVER_PORT=127.0.0.1:8000",
        ]
    return "\n".join(lines) + "\n"


def write_server_files(
    folder: Path, *, domain: str | None = None, admin: str | None = None, force: bool = False
) -> ServerFiles:
    """Write the compose file, ``.env`` and (with *domain*) Caddy's files into *folder*."""
    from testo_core.accounts import AccountError, check_new_account

    domain = (domain or "").strip().lower() or None
    if domain and not _DOMAIN.match(domain):
        raise ServerSetupError(
            f"--domain must be a host name like testo.example.com, not {domain!r}"
        )
    if admin:
        try:
            admin = check_new_account(admin, "admin")
        except AccountError as exc:
            raise ServerSetupError(f"--admin: {exc}") from None
    files: dict[str, str] = {
        "docker-compose.yml": compose_yaml(),
        ".env": env_file(
            domain=domain,
            admin=admin,
            token=secrets.token_hex(32),
            db_password=secrets.token_hex(16),
            admin_password=secrets.token_urlsafe(18),
            session_key=secrets.token_hex(32),
        ),
    }
    if domain:
        files["Caddyfile"] = caddyfile(domain)
        files["docker-compose.override.yml"] = override_yaml()
    existing = [name for name in files if (folder / name).exists()]
    if existing and not force:
        raise ServerSetupError(
            f"{', '.join(existing)} already in {folder}; --force overwrites (and makes a new token)"
        )
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = folder / name
        if name == ".env":
            # Created private, not chmod-ed after: the secrets are never readable by others.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(path, 0o600)
        else:
            path.write_text(text, encoding="utf-8")
    url = f"https://{domain}" if domain else "http://localhost:8000"
    return ServerFiles(folder=folder, written=list(files), url=url, admin=admin)
