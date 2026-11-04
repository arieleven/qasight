"""Per-test results as rows: the ``test_result`` table.

One row per test per run, written when a run is recorded. Runs recorded before
this revision are not backfilled here (their snapshots can be large and live on
disk); :mod:`qasight.history.cases` fills a run's rows from its snapshot the
first time something asks for them.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlmodel.sql.sqltypes import AutoString

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TABLE = "test_result"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE in inspector.get_table_names():
        return  # a dev checkout that ran the model before the revision existed
    op.create_table(
        _TABLE,
        sa.Column("run_id", AutoString(length=64), primary_key=True),
        sa.Column("key", AutoString(length=200), primary_key=True),
        sa.Column("name", AutoString(length=120), nullable=False),
        sa.Column("full_name", AutoString(length=1024), nullable=False),
        sa.Column("group", AutoString(length=100), nullable=False),
        sa.Column("stage", AutoString(length=128), nullable=False),
        sa.Column("framework", AutoString(length=32), nullable=False),
        sa.Column("status", AutoString(length=16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("message", AutoString(length=300), nullable=False),
    )
    op.create_index("ix_test_result_run_id", _TABLE, ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_test_result_run_id", table_name=_TABLE)
    op.drop_table(_TABLE)
