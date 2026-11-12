"""The release scorecard: ``scorecard_result`` (one tool's score for a project version) and ``scorecard_tool``.

``scorecard_tool`` keeps which tools are switched on; a tool with no row uses
its default. See :mod:`qasight.services.scorecard`.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel.sql.sqltypes import AutoString

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    # A database made by create_all before this revision existed already has them.
    if "scorecard_result" not in tables:
        op.create_table(
            "scorecard_result",
            sa.Column("id", AutoString(length=64), nullable=False),
            sa.Column("project", AutoString(length=255), nullable=False),
            sa.Column("version", AutoString(length=128), nullable=False),
            sa.Column("tool", AutoString(length=64), nullable=False),
            sa.Column("score", sa.Integer(), nullable=False),
            sa.Column("summary", AutoString(length=500), nullable=False),
            sa.Column("details", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
            sa.Column("commit_sha", AutoString(length=64), nullable=True),
            sa.Column("source", AutoString(length=32), nullable=False),
            sa.Column("recorded_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("scorecard_result", schema=None) as batch_op:
            batch_op.create_index("ix_scorecard_result_project", ["project"], unique=False)
    if "scorecard_tool" not in tables:
        op.create_table(
            "scorecard_tool",
            sa.Column("tool", AutoString(length=64), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("tool"),
        )


def downgrade() -> None:
    op.drop_table("scorecard_tool")
    with op.batch_alter_table("scorecard_result", schema=None) as batch_op:
        batch_op.drop_index("ix_scorecard_result_project")
    op.drop_table("scorecard_result")
