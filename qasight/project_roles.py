"""Per-project roles: an account's role on the cycles that match a pattern (``TESTO_AUTH=accounts``).

A team server shared by several teams can let each team change only its own
cycles. ``qasight users grant alice runner 'shop-*'`` gives ``alice`` the runner
role on every cycle whose name starts with ``shop-``, whatever her server-wide
role is; ``qasight users grant bob viewer 'payments'`` keeps ``bob``, a runner
elsewhere, from changing the ``payments`` cycle.

On a cycle, an account's role is the one of its most specific matching pattern
(an exact name beats a glob, a longer glob beats a shorter one); with no matching
pattern it is the account's server-wide role. The API token is an admin everywhere.

The role is checked on writes that name a cycle: starting a run, uploading or
streaming results (``POST /api/v1/ingest``, live results, ReportPortal agents),
marking a cause and quarantining a test. Reads stay server-wide: every signed-in
account can see every cycle. Server settings stay with the server-wide admin role.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Any

from qasight.accounts import ROLES

MAX_PATTERN = 255


class ProjectRoleError(ValueError):
    """An unknown role or an empty pattern."""


@dataclass(frozen=True)
class ProjectRole:
    username: str
    pattern: str
    role: str
    granted_by: str | None
    granted_at: datetime


def _specificity(pattern: str) -> tuple[int, int]:
    glob = any(ch in pattern for ch in "*?[")
    return (0 if glob else 1, len(pattern.replace("*", "")))


def role_on(grants: list[ProjectRole], cycle: str, default: str) -> str:
    """The role these grants give on *cycle*; *default* when none matches."""
    matching = [g for g in grants if fnmatchcase(cycle, g.pattern)]
    if not matching:
        return default
    return max(matching, key=lambda g: _specificity(g.pattern)).role


class ProjectRoleStore:
    """Grants on a SQLAlchemy engine (the run-history database by default)."""

    def __init__(self, engine: Any = None) -> None:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        self._engine = engine if engine is not None else get_engine()
        create_db_and_tables(self._engine)

    def grant(
        self, *, username: str, pattern: str, role: str, granted_by: str | None = None
    ) -> ProjectRole:
        from sqlmodel import Session

        from qasight.repository.models import ProjectRole as Row

        pattern = pattern.strip()
        if role not in ROLES:
            raise ProjectRoleError(f"role must be one of {', '.join(ROLES)}")
        if not pattern or len(pattern) > MAX_PATTERN:
            raise ProjectRoleError(f"a pattern is 1 to {MAX_PATTERN} characters")
        row = Row(
            username=username,
            pattern=pattern,
            role=role,
            granted_by=granted_by,
            granted_at=datetime.now(tz=UTC),
        )
        with Session(self._engine) as session:
            session.merge(row)
            session.commit()
        return ProjectRole(username, pattern, role, granted_by, row.granted_at)

    def revoke(self, *, username: str, pattern: str) -> bool:
        from sqlmodel import Session

        from qasight.repository.models import ProjectRole as Row

        with Session(self._engine) as session:
            row = session.get(Row, (username, pattern.strip()))
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def list(self, username: str | None = None) -> list[ProjectRole]:
        from sqlmodel import Session, col, select

        from qasight.repository.models import ProjectRole as Row

        query = select(Row).order_by(col(Row.username), col(Row.pattern))
        if username:
            query = query.where(Row.username == username)
        with Session(self._engine) as session:
            return [
                ProjectRole(
                    r.username,
                    r.pattern,
                    r.role,
                    r.granted_by,
                    r.granted_at if r.granted_at.tzinfo else r.granted_at.replace(tzinfo=UTC),
                )
                for r in session.exec(query).all()
            ]

    def remove_user(self, username: str) -> int:
        grants = self.list(username)
        for grant in grants:
            self.revoke(username=username, pattern=grant.pattern)
        return len(grants)
