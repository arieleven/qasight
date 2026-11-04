"""Service locator for the process-wide :class:`~qasight.repository.base.BaseRunRepository`."""

from __future__ import annotations

from functools import lru_cache

from qasight.repository.base import BaseRunRepository
from qasight.repository.db_config import create_db_and_tables, get_engine, resolve_database_url
from qasight.repository.factory import create_repository_for_url
from qasight.repository.report_archive_repository import SQLReportArchiveRepository
from qasight.repository.test_results import TestResultStore


@lru_cache(maxsize=1)
def get_repository() -> BaseRunRepository:
    """Return the cached repository for the active :func:`~qasight.repository.db_config.resolve_database_url`."""
    url = resolve_database_url()
    create_db_and_tables()
    return create_repository_for_url(url=url, engine=get_engine())


@lru_cache(maxsize=1)
def get_report_archive_repository() -> SQLReportArchiveRepository:
    """Return the cached :class:`SQLReportArchiveRepository` for the active database URL."""
    create_db_and_tables()
    return SQLReportArchiveRepository(get_engine())


@lru_cache(maxsize=1)
def get_test_result_store() -> TestResultStore:
    """Return the cached :class:`TestResultStore` for the active database URL."""
    create_db_and_tables()
    return TestResultStore(get_engine())


def reset_repository_cache() -> None:
    """Clear the cached repository. Use in tests after changing database configuration."""
    get_repository.cache_clear()
    get_report_archive_repository.cache_clear()
    get_test_result_store.cache_clear()
