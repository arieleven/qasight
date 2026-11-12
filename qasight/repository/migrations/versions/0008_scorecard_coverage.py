"""The release scorecard's coverage: ``scorecard_coverage``, which tools a scan expected and which ran.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel.sql.sqltypes import AutoString

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    # A database made by create_all before this revision existed already has it.
    if "scorecard_coverage" not in tables:
        op.create_table(
            "scorecard_coverage",
            sa.Column("id", AutoString(length=64), nullable=False),
            sa.Column("project", AutoString(length=255), nullable=False),
            sa.Column("version", AutoString(length=128), nullable=False),
            sa.Column("details", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
            sa.Column("recorded_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("scorecard_coverage")
