"""Schema migrations for the run history database (Alembic, run in-process).

Every table QASight owns is described twice: by the SQLModel classes in
:mod:`qasight.repository.models`, which the code reads and writes through,
and by the revision scripts in ``versions/``, which are the only thing that
changes a database that already has data. ``SQLModel.metadata.create_all()`` is
no longer called on a real database: :func:`upgrade_to_head` is.

Rules:

- A model change ships with a revision in the same pull request. The test
  ``tests/unit/qasight/repository/test_migrations.py`` compares the two and
  fails when they differ.
- Databases created by ``create_all()`` before revision ``0001`` existed (every
  install up to 1.5.0) have the tables but no ``alembic_version`` row. Revision
  ``0001`` adopts them: it creates only the tables and columns that are missing
  (with defaults, so old rows stay valid) instead of failing on the ones that
  exist, and the later revisions apply as usual. Nothing is lost and nobody
  runs a command by hand.
- Several processes may start on one database at once (parallel CI workers
  leasing keys, ``qasight run`` next to ``qasight-api``). The first one upgrades;
  the others see a database error, find the schema already at head and go on.
- New revisions: ``python scripts/db_revision.py "add x to y"`` writes the
  script from the model diff; read it before committing, autogenerate misses
  renames and server defaults.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DatabaseError
from sqlmodel import SQLModel

logger = logging.getLogger(__name__)

SCRIPT_LOCATION = Path(__file__).resolve().parent
_VERSION_TABLE = "alembic_version"

# Alembic's own state is per Config, so running two upgrades at once in one
# process would only waste work; the lock keeps the first run exclusive.
_UPGRADE_LOCK = threading.Lock()


def alembic_config(connection: Connection | None = None) -> Config:
    """An Alembic config pointing at this package, with no ``alembic.ini``."""
    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPT_LOCATION))
    # Revision scripts are named by number, not by hash, so the history reads in order.
    cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def head_revision() -> str:
    """The newest revision in ``versions/``."""
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("no migration scripts found")
    return head


def current_revision(connection: Connection) -> str | None:
    """The revision the database is stamped at, or None when it has no version table."""
    return MigrationContext.configure(connection).get_current_revision()


def is_legacy_schema(connection: Connection) -> bool:
    """True for a database built before migrations existed: some of our tables, no version table.

    Any of them counts: a database first opened by ``qasight lease`` has the
    resource tables and no ``runrecord``.
    """
    import qasight.repository.models  # noqa: F401  (registers every table)

    tables = set(inspect(connection).get_table_names())
    if _VERSION_TABLE in tables:
        return False
    return any(name in tables for name in SQLModel.metadata.tables)


def _upgrade(connection: Connection) -> str:
    if is_legacy_schema(connection):
        logger.info("run history database predates migrations; adopting it (revision 0001)")
    before = current_revision(connection)
    head = head_revision()
    if before != head:
        command.upgrade(alembic_config(connection), "head")
        logger.info("run history database upgraded from %s to %s", before or "empty", head)
    return head


def upgrade_to_head(engine: Engine, *, wait_s: float = 30.0) -> str:
    """Bring ``engine``'s database to the newest revision and return it.

    Idempotent: a database already at head is left alone after one query. When
    another process is upgrading the same database (parallel CI workers taking
    leases), this one's attempt fails on the lock or the half-built table; it
    then waits up to ``wait_s`` for the other to finish and goes on.
    """
    deadline = time.monotonic() + wait_s
    first_error: DatabaseError | None = None
    with _UPGRADE_LOCK:
        while True:
            try:
                with engine.begin() as connection:
                    return _upgrade(connection)
            except DatabaseError as exc:
                first_error = first_error or exc
                with engine.connect() as connection:
                    if _at_head(connection):
                        return head_revision()
                if time.monotonic() >= deadline:
                    # Not a race after all: the first failure says what is wrong.
                    raise first_error from None
                logger.debug("schema upgrade lost a race (%s); waiting for the winner", exc)
                time.sleep(0.2)


def _at_head(connection: Connection) -> bool:
    try:
        return current_revision(connection) == head_revision()
    except DatabaseError:  # the other process holds the lock; try again shortly
        return False
