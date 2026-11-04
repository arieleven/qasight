"""Database wiring for mock API integration tests.

Reads ``database.url`` from the repository ``qasight.yaml`` (same discovery rules as
``qasight run``) when ``DATABASE_URL`` is not already set. Ensures tables exist so code paths that
touch ``qasight.repository.db`` / run history behave like production without requiring manual env setup.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def _configure_database_url_for_sandbox_integration() -> None:
    if not os.getenv("DATABASE_URL", "").strip():
        from qasight.config.database_section import database_url_from_discovered_config
        from qasight.repository.db import reset_repository_cache
        from qasight.repository.db_config import create_db_and_tables, reset_engine_cache

        url = database_url_from_discovered_config(cwd=_REPO_ROOT)
        if not url:
            db_path = _REPO_ROOT / "artifacts" / "testo_sandbox.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{db_path.resolve()}"

        os.environ["DATABASE_URL"] = url
        reset_engine_cache()
        reset_repository_cache()
        create_db_and_tables()

    yield
