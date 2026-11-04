"""The run history schema is owned by the migrations, and the models must agree with them."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from qasight.repository import migrations

pytestmark = pytest.mark.tier_fast

EXPECTED_TABLES = {
    "runrecord",
    "reportarchive",
    "queued_run",
    "user_account",
    "audit_event",
    "failure_triage",
    "resource_item",
    "resource_lease",
    "resource_action",
    "resource_waiter",
    "test_result",
}


def _engine(tmp_path: Path):
    return create_engine(f"sqlite:///{tmp_path / 'history.db'}")


def test_fresh_database_gets_every_table_and_the_head_revision(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    head = migrations.upgrade_to_head(engine)

    assert head == migrations.head_revision()
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    with engine.connect() as connection:
        assert migrations.current_revision(connection) == head


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    migrations.upgrade_to_head(engine)
    migrations.upgrade_to_head(engine)
    with engine.connect() as connection:
        rows = connection.execute(text("select count(*) from alembic_version")).scalar_one()
    assert rows == 1


def test_models_and_migrations_describe_the_same_schema(tmp_path: Path) -> None:
    """A model change without a revision (or the reverse) fails here, not on a user's database."""
    engine = _engine(tmp_path)
    migrations.upgrade_to_head(engine)
    import qasight.repository.models  # noqa: F401  (registers the tables)

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "render_as_batch": True}
        )
        diff = compare_metadata(context, SQLModel.metadata)
    assert diff == [], f"models and migrations differ: {diff}"


def test_legacy_create_all_database_is_stamped_then_upgraded(tmp_path: Path) -> None:
    """Databases from 1.5.0 and earlier have the tables but no alembic_version."""
    engine = _engine(tmp_path)
    import qasight.repository.models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into runrecord (id, status, metadata) "
                "values ('00000000000000000000000000000001', 'COMPLETED', '{\"name\": \"smoke\"}')"
            )
        )
        assert migrations.is_legacy_schema(connection)

    head = migrations.upgrade_to_head(engine)

    with engine.connect() as connection:
        assert not migrations.is_legacy_schema(connection)
        assert migrations.current_revision(connection) == head
        kept = connection.execute(text("select count(*) from runrecord")).scalar_one()
    assert kept == 1


def test_revision_files_are_numbered_in_order() -> None:
    versions = sorted((migrations.SCRIPT_LOCATION / "versions").glob("[0-9]*.py"))
    numbers = [int(p.name[:4]) for p in versions]
    assert numbers == list(range(1, len(numbers) + 1)), versions


def test_legacy_database_missing_tables_and_columns_is_completed(tmp_path: Path) -> None:
    """A 1.3-era database: no resource tables, reportarchive without its counters."""
    engine = _engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "create table runrecord (id char(32) primary key, status varchar(9) not null, "
                "start_time datetime, end_time datetime, metadata json not null)"
            )
        )
        connection.execute(
            text(
                "create table reportarchive (id char(32) primary key, cycle_name varchar(512) "
                "not null, created_at datetime not null, exit_code integer not null, "
                "summary_json json not null, artifact_bytes blob not null)"
            )
        )
        connection.execute(
            text(
                "insert into reportarchive values ('00000000000000000000000000000002', 'smoke', "
                "'2026-01-01 00:00:00', 0, '{}', x'00')"
            )
        )

    migrations.upgrade_to_head(engine)

    inspector = inspect(engine)
    assert EXPECTED_TABLES <= set(inspector.get_table_names())
    archive_columns = {c["name"] for c in inspector.get_columns("reportarchive")}
    assert {"total_tests", "passed", "failed", "plan_duration_ms"} <= archive_columns
    item_columns = {c["name"] for c in inspector.get_columns("resource_item")}
    assert "clean" in item_columns
    with engine.connect() as connection:
        kept = connection.execute(text("select total_tests from reportarchive")).scalar_one()
    assert kept is None


def test_two_processes_upgrading_at_once_both_succeed(tmp_path: Path) -> None:
    """Parallel workers lease keys at the same moment; the loser must not crash."""
    engine = _engine(tmp_path)
    migrations.upgrade_to_head(engine)
    # Simulate the loser: the schema is already at head when its own attempt errors out.
    original = migrations._upgrade

    def _losing_upgrade(connection):  # noqa: ANN001, ANN202
        from sqlalchemy.exc import OperationalError

        raise OperationalError("create table", {}, Exception("table already exists"))

    migrations._upgrade = _losing_upgrade  # type: ignore[assignment]
    try:
        assert migrations.upgrade_to_head(engine) == migrations.head_revision()
    finally:
        migrations._upgrade = original  # type: ignore[assignment]


def test_revision_0002_fills_the_promoted_columns_from_metadata(tmp_path: Path) -> None:
    """Rows written before the columns existed query like new ones after the upgrade."""
    from alembic import command

    engine = _engine(tmp_path)
    with engine.begin() as connection:
        command.upgrade(migrations.alembic_config(connection), "0001")
        connection.execute(
            text(
                "insert into runrecord (id, status, start_time, metadata) values "
                "('00000000000000000000000000000003', 'COMPLETED', '2026-01-01 00:00:00', "
                '\'{"plan": "smoke", "health_pct": 87.5, "exit_code": 1, '
                '"ci_ref_name": "main", "git_branch": "local", "git_commit": "abc123", '
                '"source": "engine", "snapshot_dir": "static/history/x/artifacts"}\')'
            )
        )
        connection.execute(
            text(
                "insert into runrecord (id, status, start_time, metadata) values "
                "('00000000000000000000000000000004', 'FAILED', '2026-01-02 00:00:00', '{}')"
            )
        )

    migrations.upgrade_to_head(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "select cycle, health_pct, exit_code, branch, commit_sha, source, snapshot_dir "
                "from runrecord order by start_time"
            )
        ).fetchall()
    assert tuple(rows[0]) == (
        "smoke",
        87.5,
        1,
        "main",
        "abc123",
        "engine",
        "static/history/x/artifacts",
    )
    assert tuple(rows[1]) == (None, None, None, None, None, None, None)
