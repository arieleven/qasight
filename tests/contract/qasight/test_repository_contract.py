from __future__ import annotations

import pytest

from qasight.repository.base import BaseRunRepository
from qasight.repository.db import get_repository, reset_repository_cache
from qasight.repository.db_config import reset_engine_cache, validate_database_url
from qasight.repository.models import RunStatus


@pytest.mark.contract
@pytest.mark.parametrize(
    ("url", "dialect"),
    [
        ("sqlite:///:memory:", "sqlite"),
        ("postgresql+psycopg://u:p@localhost:5432/testo", "postgresql"),
        ("mysql+pymysql://u:p@localhost:3306/testo", "mysql"),
    ],
)
def test_validate_database_url_supported(url: str, dialect: str) -> None:
    assert validate_database_url(url) == dialect


@pytest.mark.contract
def test_validate_database_url_rejects_unsupported() -> None:
    with pytest.raises(ValueError, match="Unsupported database dialect `oracle`"):
        validate_database_url("oracle://u:p@localhost:1521/xe")


@pytest.mark.contract
def test_get_repository_remains_protocol_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_repository_cache()
    reset_engine_cache()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    try:
        repo = get_repository()
        assert isinstance(repo, BaseRunRepository)
        row = repo.create_run(status=RunStatus.PENDING)
        assert row.id is not None
    finally:
        reset_repository_cache()
        reset_engine_cache()
