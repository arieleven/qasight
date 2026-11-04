"""``test_result`` gains ``crash``: a failing test that died of a stack overflow or crash.

Holds ``"stack_overflow"``, ``"crash"`` or ``""`` (see
:mod:`qasight.engine.crash_signatures`). Rows written before this revision
keep ``""``; the PR comment and the run page then simply show no crash badge.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlmodel.sql.sqltypes import AutoString

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_TABLE = "test_result"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "crash" in {c["name"] for c in inspector.get_columns(_TABLE)}:
        return  # a dev checkout that ran the model before the revision existed
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("crash", AutoString(length=16), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_column("crash")
