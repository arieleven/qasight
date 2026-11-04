"""Promote the run fields every list filters on out of the JSON into indexed columns.

``runrecord`` gains ``cycle``, ``health_pct``, ``exit_code``, ``branch``,
``commit_sha``, ``source`` and ``snapshot_dir``, an index on ``start_time`` and one
on ``(cycle, start_time)``. Existing rows are filled from their ``metadata``
with the same rules the repository applies on every write
(:func:`qasight.repository.models.promoted_columns`), so old and new runs
query alike.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlmodel.sql.sqltypes import AutoString

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_BATCH = 500


_COLUMNS = (
    ("cycle", lambda: AutoString(length=512)),
    ("health_pct", sa.Float),
    ("exit_code", sa.Integer),
    ("branch", lambda: AutoString(length=255)),
    ("commit_sha", lambda: AutoString(length=64)),
    ("source", lambda: AutoString(length=32)),
    ("snapshot_dir", lambda: AutoString(length=1024)),
)
_INDEXES = (
    ("ix_runrecord_cycle", ["cycle"]),
    ("ix_runrecord_cycle_start_time", ["cycle", "start_time"]),
    ("ix_runrecord_start_time", ["start_time"]),
)


def upgrade() -> None:
    # Like 0001, adoptive: a database that already has some of this (a dev checkout that ran
    # the models before the revision existed) only gets what it lacks.
    inspector = sa.inspect(op.get_bind())
    have_columns = {c["name"] for c in inspector.get_columns("runrecord")}
    have_indexes = {i["name"] for i in inspector.get_indexes("runrecord")}
    with op.batch_alter_table("runrecord", schema=None) as batch_op:
        for name, make_type in _COLUMNS:
            if name not in have_columns:
                batch_op.add_column(sa.Column(name, make_type(), nullable=True))
        for index_name, columns in _INDEXES:
            if index_name not in have_indexes:
                batch_op.create_index(index_name, columns, unique=False)
    _backfill()


def _backfill() -> None:
    """Fill the new columns of every existing row from its ``metadata`` JSON."""
    from qasight.repository.models import promoted_columns

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, metadata FROM runrecord")).fetchall()
    update = sa.text(
        "UPDATE runrecord SET cycle = :cycle, health_pct = :health_pct, exit_code = :exit_code, "
        "branch = :branch, commit_sha = :commit_sha, source = :source, snapshot_dir = :snapshot_dir "
        "WHERE id = :id"
    )
    for start in range(0, len(rows), _BATCH):
        params = []
        for row_id, raw in rows[start : start + _BATCH]:
            metadata = raw if isinstance(raw, dict) else _loads(raw)
            params.append({"id": row_id, **promoted_columns(metadata)})
        if params:
            connection.execute(update, params)


def _loads(raw: object) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def downgrade() -> None:
    with op.batch_alter_table("runrecord", schema=None) as batch_op:
        for index_name, _columns in reversed(_INDEXES):
            batch_op.drop_index(index_name)
        for name, _make_type in reversed(_COLUMNS):
            batch_op.drop_column(name)
