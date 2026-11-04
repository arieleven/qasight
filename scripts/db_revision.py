"""Write a new run history migration from the difference between the models and a fresh database.

Usage::

    python scripts/db_revision.py "add cycle column to runrecord"

Builds the current head schema in a throwaway SQLite database, compares it with
``qasight.repository.models`` and writes ``qasight/repository/migrations/versions/<n>_<slug>.py``.
Read the script before committing it: autogenerate cannot see a rename (it
emits drop + add) and ignores server defaults. Then run
``pytest tests/unit/qasight/repository/test_migrations.py``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qasight.repository import migrations  # noqa: E402


def _next_revision_id() -> str:
    versions = migrations.SCRIPT_LOCATION / "versions"
    numbers = [int(p.name[:4]) for p in versions.glob("[0-9][0-9][0-9][0-9]_*.py")]
    return f"{(max(numbers) if numbers else 0) + 1:04d}"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].strip():
        print(__doc__)
        return 2
    message = argv[1].strip()
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(f"sqlite:///{Path(tmp) / 'scratch.db'}")
        migrations.upgrade_to_head(engine)
        rev_id = _next_revision_id()
        with engine.begin() as connection:
            command.revision(
                migrations.alembic_config(connection),
                message=message,
                autogenerate=True,
                rev_id=rev_id,
            )
    for path in (migrations.SCRIPT_LOCATION / "versions").glob(f"{rev_id}_*.py"):
        _tidy(path)
    return 0


def _tidy(path: Path) -> None:
    """Use the template's imports and drop Alembic's reminder comments."""
    text = path.read_text(encoding="utf-8")
    text = text.replace("sqlmodel.sql.sqltypes.AutoString", "AutoString")
    text = text.replace("sqlmodel.sql.sqltypes.UTCDateTime()", "sa.DateTime()")
    text = "\n".join(
        line
        for line in text.splitlines()
        if "### commands auto generated" not in line and "### end Alembic commands" not in line
    )
    path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
