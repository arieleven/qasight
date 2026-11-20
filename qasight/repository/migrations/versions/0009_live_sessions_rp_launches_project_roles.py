"""Live sessions in the database, ReportPortal-compatible launches, and per-project roles.

- ``live_session``: test sessions still running, so every API worker sees them.
- ``rp_launch``, ``rp_item``, ``rp_log``: what ReportPortal agents report to
  ``/api/v1/{project}/...`` until the launch finishes and becomes a run.
- ``project_role``: an account's role on the cycles matching a pattern.
- ``scorecard_scan``: scorecard scans started from the UI, so every worker reports them.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel.sql.sqltypes import AutoString

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def _index(table: str, column: str) -> None:
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.create_index(batch_op.f(f"ix_{table}_{column}"), [column], unique=False)


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    # A database made by create_all after the models gained these tables already has them.
    if "live_session" not in tables:
        op.create_table(
            "live_session",
            sa.Column("id", AutoString(length=32), nullable=False),
            sa.Column("cycle", AutoString(length=512), nullable=False),
            sa.Column("source", AutoString(length=32), nullable=False),
            sa.Column("started_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("finished_at", sa.Float(), nullable=True),
            sa.Column("total", sa.Integer(), nullable=True),
            sa.Column("branch", AutoString(length=200), nullable=True),
            sa.Column("commit", AutoString(length=64), nullable=True),
            sa.Column("current", AutoString(length=1000), nullable=True),
            sa.Column("counts", _JSON, nullable=False),
            sa.Column("failures", _JSON, nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        _index("live_session", "updated_at")
    if "scorecard_scan" not in tables:
        op.create_table(
            "scorecard_scan",
            sa.Column("id", AutoString(length=32), nullable=False),
            sa.Column("state", AutoString(length=16), nullable=False),
            sa.Column("started_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("finished_at", sa.Float(), nullable=True),
            sa.Column("data", _JSON, nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        _index("scorecard_scan", "state")
    if "rp_launch" not in tables:
        op.create_table(
            "rp_launch",
            sa.Column("uuid", AutoString(length=64), nullable=False),
            sa.Column("project", AutoString(length=255), nullable=False),
            sa.Column("name", AutoString(length=512), nullable=False),
            sa.Column("cycle", AutoString(length=128), nullable=False),
            sa.Column("number", sa.Integer(), nullable=False),
            sa.Column("description", AutoString(length=4000), nullable=False),
            sa.Column("mode", AutoString(length=16), nullable=False),
            sa.Column("attributes", _JSON, nullable=False),
            sa.Column("start_ms", sa.BigInteger(), nullable=False),
            sa.Column("end_ms", sa.BigInteger(), nullable=True),
            sa.Column("status", AutoString(length=16), nullable=False),
            sa.Column("live_session_id", AutoString(length=32), nullable=True),
            sa.Column("run_id", AutoString(length=64), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("uuid"),
        )
        _index("rp_launch", "project")
        _index("rp_launch", "updated_at")
    if "rp_item" not in tables:
        op.create_table(
            "rp_item",
            sa.Column("uuid", AutoString(length=64), nullable=False),
            sa.Column("launch_uuid", AutoString(length=64), nullable=False),
            sa.Column("parent_uuid", AutoString(length=64), nullable=True),
            sa.Column("name", AutoString(length=1024), nullable=False),
            sa.Column("type", AutoString(length=32), nullable=False),
            sa.Column("has_stats", sa.Boolean(), nullable=False),
            sa.Column("code_ref", AutoString(length=1024), nullable=False),
            sa.Column("test_case_id", AutoString(length=1024), nullable=False),
            sa.Column("description", AutoString(length=4000), nullable=False),
            sa.Column("attributes", _JSON, nullable=False),
            sa.Column("parameters", _JSON, nullable=False),
            sa.Column("start_ms", sa.BigInteger(), nullable=False),
            sa.Column("end_ms", sa.BigInteger(), nullable=True),
            sa.Column("status", AutoString(length=16), nullable=True),
            sa.Column("retry", sa.Boolean(), nullable=False),
            sa.Column("issue_type", AutoString(length=32), nullable=True),
            sa.Column("seq", sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint("uuid"),
        )
        _index("rp_item", "launch_uuid")
        _index("rp_item", "parent_uuid")
    if "rp_log" not in tables:
        op.create_table(
            "rp_log",
            sa.Column("uuid", AutoString(length=64), nullable=False),
            sa.Column("launch_uuid", AutoString(length=64), nullable=False),
            sa.Column("item_uuid", AutoString(length=64), nullable=True),
            sa.Column("time_ms", sa.BigInteger(), nullable=False),
            sa.Column("level", AutoString(length=16), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("attachment_name", AutoString(length=255), nullable=True),
            sa.Column("attachment_type", AutoString(length=128), nullable=True),
            sa.Column("attachment", sa.LargeBinary(), nullable=True),
            sa.PrimaryKeyConstraint("uuid"),
        )
        _index("rp_log", "launch_uuid")
        _index("rp_log", "item_uuid")
    if "project_role" not in tables:
        op.create_table(
            "project_role",
            sa.Column("username", AutoString(length=64), nullable=False),
            sa.Column("pattern", AutoString(length=255), nullable=False),
            sa.Column("role", AutoString(length=16), nullable=False),
            sa.Column("granted_by", AutoString(length=64), nullable=True),
            sa.Column("granted_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("username", "pattern"),
        )


def downgrade() -> None:
    op.drop_table("project_role")
    for table, columns in (
        ("rp_log", ("item_uuid", "launch_uuid")),
        ("rp_item", ("parent_uuid", "launch_uuid")),
        ("rp_launch", ("updated_at", "project")),
        ("live_session", ("updated_at",)),
        ("scorecard_scan", ("state",)),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for column in columns:
                batch_op.drop_index(batch_op.f(f"ix_{table}_{column}"))
        op.drop_table(table)
