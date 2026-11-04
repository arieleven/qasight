"""``test_result`` gains ``labels`` and ``links``: what a test is about and where its issues are.

``labels`` holds ``[{name, value}]`` (feature, story, severity, owner, tag and
custom labels; see :func:`qasight.reporting.allure_cases.case_labels`) and
``links`` holds ``[{name, url, type}]`` (issue and story links). Both are JSON and
nullable: rows written before this revision keep ``NULL``, read as no labels.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_TABLE = "test_result"


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    have = {c["name"] for c in inspector.get_columns(_TABLE)}
    missing = [name for name in ("labels", "links") if name not in have]
    if not missing:
        return  # a dev checkout that ran the model before the revision existed
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        for name in missing:
            batch_op.add_column(sa.Column(name, _json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_column("links")
        batch_op.drop_column("labels")
