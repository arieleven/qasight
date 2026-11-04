"""Explicit repository adapter factory by SQLAlchemy dialect."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from urllib.parse import urlparse

from sqlalchemy.engine import Engine

from qasight.repository.adapters import SQLModelRunRepository
from qasight.repository.base import BaseRunRepository
from qasight.repository.db_config import _dialect


@dataclass(frozen=True)
class SQLiteRepositoryAdapter:
    """SQLite repository strategy."""

    dialect: str = "sqlite"

    def build(self, *, engine: Engine, url: str) -> BaseRunRepository:
        del url
        return SQLModelRunRepository(engine=engine)


@dataclass(frozen=True)
class PostgreSQLRepositoryAdapter:
    """PostgreSQL repository strategy."""

    dialect: str = "postgresql"

    def build(self, *, engine: Engine, url: str) -> BaseRunRepository:
        _validate_driver_installed(url=url, dialect=self.dialect)
        return SQLModelRunRepository(engine=engine)


@dataclass(frozen=True)
class MySQLRepositoryAdapter:
    """MySQL repository strategy."""

    dialect: str = "mysql"

    def build(self, *, engine: Engine, url: str) -> BaseRunRepository:
        _validate_driver_installed(url=url, dialect=self.dialect)
        return SQLModelRunRepository(engine=engine)


SUPPORTED_DIALECT_ADAPTERS: dict[
    str, SQLiteRepositoryAdapter | PostgreSQLRepositoryAdapter | MySQLRepositoryAdapter
] = {
    "sqlite": SQLiteRepositoryAdapter(),
    "postgresql": PostgreSQLRepositoryAdapter(),
    "mysql": MySQLRepositoryAdapter(),
}


def select_repository_adapter(
    *, url: str
) -> SQLiteRepositoryAdapter | PostgreSQLRepositoryAdapter | MySQLRepositoryAdapter:
    """Return the explicit adapter strategy for ``url``."""
    dialect = _dialect(url)
    adapter = SUPPORTED_DIALECT_ADAPTERS.get(dialect)
    if adapter is None:
        allowed = ", ".join(sorted(SUPPORTED_DIALECT_ADAPTERS))
        raise ValueError(
            f"Unsupported database dialect `{dialect}`. Supported dialects: {allowed}."
        )
    return adapter


def create_repository_for_url(*, url: str, engine: Engine) -> BaseRunRepository:
    """Build a repository using explicit adapter strategy selection."""
    adapter = select_repository_adapter(url=url)
    return adapter.build(engine=engine, url=url)


def _driver_from_scheme(url: str) -> str | None:
    scheme = urlparse(url).scheme.lower()
    if "+" not in scheme:
        return None
    _, driver = scheme.split("+", 1)
    return driver or None


def _validate_driver_installed(*, url: str, dialect: str) -> None:
    """Fast-fail with deterministic messaging when a configured DB driver is missing."""
    driver = _driver_from_scheme(url)
    if dialect == "postgresql":
        module = "psycopg" if driver in (None, "psycopg") else driver
        if find_spec(module) is None:
            raise ValueError(
                f"Database URL `{url}` requires PostgreSQL driver module `{module}` which is not installed."
            )
        return

    if dialect == "mysql":
        candidate_modules = []
        if driver is not None:
            if driver == "pymysql":
                candidate_modules = ["pymysql"]
            elif driver in {"mysqldb", "mysqlclient"}:
                candidate_modules = ["MySQLdb"]
            elif driver in {"mysqlconnector", "mysql-connector-python"}:
                candidate_modules = ["mysql.connector"]
            else:
                candidate_modules = [driver]
        else:
            candidate_modules = ["pymysql", "MySQLdb", "mysql.connector"]

        if any(find_spec(module) is not None for module in candidate_modules):
            return

        readable = ", ".join(candidate_modules)
        raise ValueError(f"Database URL `{url}` requires an installed MySQL driver ({readable}).")
