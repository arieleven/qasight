"""Team server accounts and the audit log (used only when ``TESTO_AUTH=accounts``).

Accounts live in the run-history database, next to the runs. There is no sign-up:
an admin creates them with ``qasight users add``, which talks to the database
directly. Passwords are hashed with ``scrypt`` from the standard library.

Roles, from least to most:

- ``viewer``: reads runs, logs and reports.
- ``runner``: also starts runs and uploads results.
- ``admin``: also changes server settings (AI provider) and reads the audit log.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

ROLES: Final[tuple[str, ...]] = ("viewer", "runner", "admin")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")
MIN_PASSWORD_LENGTH: Final[int] = 10

# scrypt cost: n=2**14, r=8 is the common interactive-login setting (~16 MB, tens of ms).
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


class AccountError(ValueError):
    """A bad username, password or role, or an account that doesn't exist."""


def role_at_least(role: str, needed: str) -> bool:
    return role in ROLES and ROLES.index(role) >= ROLES.index(needed)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(digest_hex) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _check_username(username: str) -> str:
    name = username.strip()
    if not _USERNAME_RE.match(name):
        raise AccountError("usernames are 1-64 characters: letters, digits and . _ @ - (not first)")
    return name


def check_new_account(username: str, role: str) -> str:
    """Validate the name and role of an account about to be added (before asking for a
    password); returns the cleaned name. :class:`AccountError` if either is bad."""
    name = _check_username(username)
    _check_role(role)
    return name


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"passwords need at least {MIN_PASSWORD_LENGTH} characters")


def _check_role(role: str) -> str:
    if role not in ROLES:
        raise AccountError(f"role must be one of {', '.join(ROLES)}")
    return role


@dataclass(frozen=True)
class Account:
    username: str
    role: str
    session_version: int
    created_at: datetime


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    username: str
    method: str
    path: str
    status: int


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class AccountStore:
    """Accounts and audit events on a SQLAlchemy engine (the run-history DB by default)."""

    def __init__(self, engine: Any = None) -> None:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        self._engine = engine if engine is not None else get_engine()
        create_db_and_tables(self._engine)

    # --- accounts ---------------------------------------------------------

    def add(self, username: str, password: str, role: str = "viewer") -> Account:
        from sqlmodel import Session

        from qasight.repository.models import UserAccount

        name = _check_username(username)
        _check_password(password)
        _check_role(role)
        with Session(self._engine) as session:
            if session.get(UserAccount, name) is not None:
                raise AccountError(f"user {name!r} already exists")
            row = UserAccount(username=name, password_hash=hash_password(password), role=role)
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._account(row)

    def get(self, username: str) -> Account | None:
        from sqlmodel import Session

        from qasight.repository.models import UserAccount

        with Session(self._engine) as session:
            row = session.get(UserAccount, username)
            return None if row is None else self._account(row)

    def all(self) -> list[Account]:
        from sqlmodel import Session, col, select

        from qasight.repository.models import UserAccount

        with Session(self._engine) as session:
            rows = session.exec(select(UserAccount).order_by(col(UserAccount.username))).all()
            return [self._account(row) for row in rows]

    def authenticate(self, username: str, password: str) -> Account | None:
        from sqlmodel import Session

        from qasight.repository.models import UserAccount

        with Session(self._engine) as session:
            row = session.get(UserAccount, username.strip())
            if row is None:
                # Same work as a real check, so timing doesn't say which names exist.
                verify_password(password, _DUMMY_HASH)
                return None
            return self._account(row) if verify_password(password, row.password_hash) else None

    def set_role(self, username: str, role: str) -> Account:
        _check_role(role)
        return self._update(username, role=role)

    def reset_password(self, username: str, password: str) -> Account:
        _check_password(password)
        return self._update(username, password_hash=hash_password(password))

    def sign_out_everywhere(self, username: str) -> Account:
        return self._update(username)

    def remove(self, username: str) -> None:
        from sqlmodel import Session

        from qasight.repository.models import UserAccount

        with Session(self._engine) as session:
            row = session.get(UserAccount, username)
            if row is None:
                raise AccountError(f"no user {username!r}")
            session.delete(row)
            session.commit()

    def _update(self, username: str, **fields: Any) -> Account:
        """Change ``fields`` and bump the session version (every change signs the user out)."""
        from sqlmodel import Session

        from qasight.repository.models import UserAccount

        with Session(self._engine) as session:
            row = session.get(UserAccount, username)
            if row is None:
                raise AccountError(f"no user {username!r}")
            for key, value in fields.items():
                setattr(row, key, value)
            row.session_version += 1
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._account(row)

    @staticmethod
    def _account(row: Any) -> Account:
        return Account(
            username=row.username,
            role=row.role,
            session_version=row.session_version,
            created_at=_aware(row.created_at),
        )

    # --- audit log --------------------------------------------------------

    def record(self, *, username: str, method: str, path: str, status: int) -> None:
        from sqlmodel import Session

        from qasight.repository.models import AuditEvent

        with Session(self._engine) as session:
            session.add(
                AuditEvent(username=username, method=method, path=path[:1024], status=status)
            )
            session.commit()

    def recent(self, limit: int = 100) -> list[AuditEntry]:
        from sqlmodel import Session, col, select

        from qasight.repository.models import AuditEvent

        with Session(self._engine) as session:
            rows = session.exec(
                select(AuditEvent).order_by(col(AuditEvent.at).desc()).limit(max(1, limit))
            ).all()
            return [AuditEntry(_aware(r.at), r.username, r.method, r.path, r.status) for r in rows]


_DUMMY_HASH: Final[str] = hash_password("not-a-real-password")


def ensure_first_admin(
    env: dict[str, str] | None = None, *, store: AccountStore | None = None
) -> str | None:
    """Create ``TESTO_ADMIN_USER`` as admin when ``TESTO_AUTH=accounts`` and no account exists.

    For a fresh team server (``qasight server init`` writes both variables to
    ``.env``), so nobody has to ``docker compose exec`` into it to add the first
    user. Does nothing once any account exists, so a changed password in ``.env``
    never overwrites one. Returns the name it created.
    """
    import os

    env = dict(os.environ if env is None else env)
    if env.get("TESTO_AUTH", "").strip().lower() != "accounts":
        return None
    user = env.get("TESTO_ADMIN_USER", "").strip()
    password = env.get("TESTO_ADMIN_PASSWORD", "")
    if not user or not password:
        return None
    store = store or AccountStore()
    if store.all():
        return None
    return store.add(user, password, role="admin").username
