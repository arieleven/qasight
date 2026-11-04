"""Alembic environment: runs on the connection the application passes in.

Loaded by Alembic for every ``upgrade``/``stamp``/``revision`` call. The
application (``qasight.repository.migrations.upgrade_to_head``) hands over
its own connection in ``config.attributes["connection"]``, so migrations run on
the same engine, URL and (for SQLite in-memory tests) the same database as the
code that follows. ``scripts/db_revision.py`` does the same for autogenerate.
"""

from __future__ import annotations

from alembic import context
from sqlmodel import SQLModel

# Importing the models registers every table on SQLModel.metadata.
import qasight.repository.models  # noqa: F401

target_metadata = SQLModel.metadata


def _run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError(
            "qasight migrations need a live connection in config.attributes['connection']; "
            "use qasight.repository.migrations.upgrade_to_head() or scripts/db_revision.py"
        )
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER most things in place; batch mode rebuilds the table instead.
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline (--sql) migrations are not supported for qasight")
_run_migrations_online()
