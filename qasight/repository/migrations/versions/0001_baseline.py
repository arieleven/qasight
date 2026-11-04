"""Baseline: the schema every install up to 1.5.0 got from ``create_all()``.

This revision also adopts databases that exist already. Up to 1.5.0 the tables
were created by ``SQLModel.metadata.create_all()`` and a few stores added their
own late columns at startup, so a database can have any subset of this schema:
all of it (1.5.0), no resource tables (1.3), ``reportarchive`` without its
counters (1.1). ``upgrade()`` creates what is missing and adds the columns a
table lacks, with a default so existing rows stay valid, then the later
revisions apply as to any database. Nothing is dropped.

Revision ID: 0001
Revises:
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Callable

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlmodel.sql.sqltypes import AutoString

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


# Column factories: a Column can belong to one Table only, so each call builds fresh ones.
_TABLES: dict[str, Callable[[], list[sa.Column]]] = {
    "audit_event": lambda: [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("username", AutoString(length=64), nullable=False),
        sa.Column("method", AutoString(length=8), nullable=False),
        sa.Column("path", AutoString(length=1024), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
    ],
    "failure_triage": lambda: [
        sa.Column("id", AutoString(length=64), primary_key=True),
        sa.Column("cycle", AutoString(length=512), nullable=False),
        sa.Column("signature", AutoString(length=1024), nullable=False),
        sa.Column("cause", AutoString(length=32), nullable=False),
        sa.Column("note", AutoString(length=500), nullable=False),
        sa.Column("example_message", AutoString(length=1000), nullable=False),
        sa.Column("run_id", AutoString(length=64), nullable=False),
        sa.Column("test_key", AutoString(length=200), nullable=False),
        sa.Column("marked_by", AutoString(length=64), nullable=True),
        sa.Column("marked_at", sa.DateTime(), nullable=False),
    ],
    "queued_run": lambda: [
        sa.Column("id", AutoString(length=64), primary_key=True),
        sa.Column("cycle", AutoString(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("request_json", _json(), nullable=False),
    ],
    "reportarchive": lambda: [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cycle_name", AutoString(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=False),
        sa.Column("summary_json", _json(), nullable=False),
        sa.Column("artifact_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("total_tests", sa.Integer(), nullable=True),
        sa.Column("passed", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=True),
        sa.Column("broken", sa.Integer(), nullable=True),
        sa.Column("skipped", sa.Integer(), nullable=True),
        sa.Column("unknown", sa.Integer(), nullable=True),
        sa.Column("allure_duration_ms", sa.Integer(), nullable=True),
        sa.Column("plan_duration_ms", sa.Integer(), nullable=True),
    ],
    "resource_action": lambda: [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.Float(), nullable=False),
        sa.Column("pool", AutoString(length=128), nullable=False),
        sa.Column("item", AutoString(length=128), nullable=False),
        sa.Column("verb", AutoString(length=16), nullable=False),
        sa.Column("user", AutoString(length=64), nullable=False),
        sa.Column("host", AutoString(length=255), nullable=False),
        sa.Column("command", AutoString(length=500), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("detail", AutoString(length=500), nullable=False),
    ],
    "resource_item": lambda: [
        sa.Column("pool", AutoString(length=128), primary_key=True),
        sa.Column("item", AutoString(length=128), primary_key=True),
        sa.Column("labels", _json(), nullable=False),
        sa.Column("data", _json(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("disabled_reason", AutoString(length=500), nullable=False),
        sa.Column("disabled_by", AutoString(length=64), nullable=True),
        sa.Column("seen_at", sa.Float(), nullable=False),
        sa.Column("clean", sa.Boolean(), nullable=False),
    ],
    "resource_lease": lambda: [
        sa.Column("pool", AutoString(length=128), primary_key=True),
        sa.Column("item", AutoString(length=128), primary_key=True),
        sa.Column("lease_id", AutoString(length=64), nullable=False),
        sa.Column("holder_user", AutoString(length=64), nullable=False),
        sa.Column("holder_host", AutoString(length=255), nullable=False),
        sa.Column("holder_pid", sa.Integer(), nullable=False),
        sa.Column("run_id", AutoString(length=128), nullable=False),
        sa.Column("stage", AutoString(length=128), nullable=False),
        sa.Column("worker", AutoString(length=64), nullable=False),
        sa.Column("note", AutoString(length=200), nullable=False),
        sa.Column("taken_at", sa.Float(), nullable=False),
        sa.Column("heartbeat_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("ttl_s", sa.Float(), nullable=False),
        sa.Column("reuse_delay_s", sa.Float(), nullable=False),
    ],
    "resource_waiter": lambda: [
        sa.Column("id", AutoString(length=64), primary_key=True),
        sa.Column("pool", AutoString(length=128), nullable=False),
        sa.Column("candidates", _json(), nullable=False),
        sa.Column("holder_user", AutoString(length=64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("heartbeat_at", sa.Float(), nullable=False),
    ],
    "runrecord": lambda: [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "RUNNING", "COMPLETED", "FAILED", name="runstatus"),
            nullable=False,
        ),
        sa.Column("start_time", sa.DateTime(), nullable=True),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.Column("metadata", _json(), nullable=False),
    ],
    "user_account": lambda: [
        sa.Column("username", AutoString(length=64), primary_key=True),
        sa.Column("password_hash", AutoString(length=512), nullable=False),
        sa.Column("role", AutoString(length=16), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    ],
}

# (index name, table, columns, unique), named as create_all() named them.
_INDEXES: list[tuple[str, str, list[str], bool]] = [
    ("ix_audit_event_at", "audit_event", ["at"], False),
    ("ix_audit_event_username", "audit_event", ["username"], False),
    ("ix_failure_triage_cycle", "failure_triage", ["cycle"], False),
    ("ix_queued_run_created_at", "queued_run", ["created_at"], False),
    ("ix_queued_run_cycle", "queued_run", ["cycle"], False),
    ("ix_reportarchive_created_at", "reportarchive", ["created_at"], False),
    ("ix_reportarchive_cycle_name", "reportarchive", ["cycle_name"], False),
    ("ix_reportarchive_id", "reportarchive", ["id"], False),
    ("ix_reportarchive_plan_duration_ms", "reportarchive", ["plan_duration_ms"], False),
    ("ix_reportarchive_total_tests", "reportarchive", ["total_tests"], False),
    ("ix_resource_action_at", "resource_action", ["at"], False),
    ("ix_resource_action_pool", "resource_action", ["pool"], False),
    ("ix_resource_lease_expires_at", "resource_lease", ["expires_at"], False),
    ("ix_resource_lease_lease_id", "resource_lease", ["lease_id"], True),
    ("ix_resource_waiter_created_at", "resource_waiter", ["created_at"], False),
    ("ix_resource_waiter_pool", "resource_waiter", ["pool"], False),
    ("ix_runrecord_id", "runrecord", ["id"], False),
    ("ix_runrecord_status", "runrecord", ["status"], False),
]


def _fill_for(column: sa.Column) -> str | None:
    """A literal ``DEFAULT`` for adding a NOT NULL column to a table that has rows."""
    if column.nullable:
        return None
    kind = column.type
    if isinstance(kind, sa.Boolean):
        return "false"
    if isinstance(kind, (sa.Integer, sa.Float)):
        return "0"
    if isinstance(kind, sa.JSON):
        return "'{}'"
    return "''"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for name, columns in _TABLES.items():
        if name not in existing_tables:
            op.create_table(name, *columns())
            continue
        have = {c["name"] for c in inspector.get_columns(name)}
        for column in columns():
            if column.name in have:
                continue
            fill = _fill_for(column)
            if fill is not None:
                column.server_default = sa.DefaultClause(sa.text(fill))
            op.add_column(name, column)
    for index_name, table, index_columns, unique in _INDEXES:
        if table in existing_tables:
            have_indexes = {i["name"] for i in inspector.get_indexes(table)}
            if index_name in have_indexes:
                continue
        op.create_index(index_name, table, index_columns, unique=unique)


def downgrade() -> None:
    for index_name, table, _columns, _unique in reversed(_INDEXES):
        op.drop_index(index_name, table_name=table)
    for name in reversed(list(_TABLES)):
        op.drop_table(name)
