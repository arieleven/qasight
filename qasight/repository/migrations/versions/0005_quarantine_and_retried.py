"""Quarantined tests set from the UI, API or CLI (``quarantined_test``), and ``test_result.retried``.

``retried`` marks a test that failed and then passed on a retry inside one run
(JUnit reruns, Surefire ``flakyFailure``), the "passed on retry" flaky signal.
Rows written before this revision keep ``False``.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlmodel.sql.sqltypes import AutoString

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "retried" not in {c["name"] for c in inspector.get_columns("test_result")}:
        with op.batch_alter_table("test_result", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("retried", sa.Boolean(), nullable=False, server_default=sa.false())
            )
    # A database made by create_all before this revision existed already has it.
    if "quarantined_test" not in inspector.get_table_names():
        op.create_table(
            "quarantined_test",
            sa.Column("id", AutoString(length=64), nullable=False),
            sa.Column("cycle", AutoString(length=512), nullable=False),
            sa.Column("test", AutoString(length=1024), nullable=False),
            sa.Column("reason", AutoString(length=500), nullable=False),
            sa.Column("owner", AutoString(length=200), nullable=False),
            sa.Column("added_by", AutoString(length=64), nullable=True),
            sa.Column("added_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("quarantined_test", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_quarantined_test_cycle"), ["cycle"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("quarantined_test", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quarantined_test_cycle"))
    op.drop_table("quarantined_test")
    with op.batch_alter_table("test_result", schema=None) as batch_op:
        batch_op.drop_column("retried")
