from __future__ import annotations

import pytest
from sqlmodel import create_engine

from qasight.repository.adapters import SQLModelRunRepository
from qasight.repository.factory import (
    MySQLRepositoryAdapter,
    PostgreSQLRepositoryAdapter,
    SQLiteRepositoryAdapter,
    create_repository_for_url,
    select_repository_adapter,
)


@pytest.mark.parametrize(
    ("url", "adapter_type"),
    [
        ("sqlite:///:memory:", SQLiteRepositoryAdapter),
        ("postgresql+psycopg://user:pass@localhost:5432/testo", PostgreSQLRepositoryAdapter),
        ("mysql+pymysql://user:pass@localhost:3306/testo", MySQLRepositoryAdapter),
    ],
)
def test_select_repository_adapter_supported(url: str, adapter_type: type[object]) -> None:
    adapter = select_repository_adapter(url=url)
    assert isinstance(adapter, adapter_type)


def test_select_repository_adapter_rejects_unsupported() -> None:
    with pytest.raises(ValueError, match="Unsupported database dialect `oracle`"):
        select_repository_adapter(url="oracle://user:pass@localhost:1521/xe")


def test_create_repository_for_url_returns_sqlmodel_repo() -> None:
    repo = create_repository_for_url(
        url="sqlite:///:memory:", engine=create_engine("sqlite:///:memory:")
    )
    assert isinstance(repo, SQLModelRunRepository)


def test_postgresql_adapter_requires_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qasight.repository.factory.find_spec",
        lambda name: None if name == "psycopg" else object(),
    )
    adapter = PostgreSQLRepositoryAdapter()

    with pytest.raises(ValueError, match="requires PostgreSQL driver module `psycopg`"):
        adapter.build(
            engine=create_engine("sqlite:///:memory:"),
            url="postgresql+psycopg://u:p@localhost:5432/db",
        )


def test_mysql_adapter_requires_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("qasight.repository.factory.find_spec", lambda _name: None)
    adapter = MySQLRepositoryAdapter()

    with pytest.raises(ValueError, match="requires an installed MySQL driver"):
        adapter.build(
            engine=create_engine("sqlite:///:memory:"), url="mysql+pymysql://u:p@localhost:3306/db"
        )
