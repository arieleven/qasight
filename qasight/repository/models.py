"""SQLModel table definitions for run history (dialect-portable JSON metadata)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, Index, LargeBinary
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

_METADATA_JSON_COL = JSON().with_variant(JSONB(), "postgresql")


class RunStatus(str, Enum):
    """Lifecycle states persisted for each orchestration run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RunRecord(SQLModel, table=True):
    """
    Canonical DB record for run lifecycle.

    Notes:
    - We use a deterministic UUID derived from the external ``run_id`` (from env) so we can upsert
      without relying on a separate unique column.
    - ``metadata`` is stored as JSON (JSONB on PostgreSQL); Python attribute is ``metadata_`` to
      avoid clashing with SQLAlchemy's ``.metadata``.
    - The fields every list and rollup filters or sorts on (``cycle``, ``health_pct``,
      ``exit_code``, ``branch``, ``commit_sha``, ``source``, ``snapshot_dir``) are also real,
      indexed columns, copied from the JSON by :func:`promoted_columns` on every write. The
      JSON stays the full record; the columns are what SQL can search without decoding it.
    """

    # A reloader (``qasight-api`` with TESTO_API_RELOAD=1) can import this module twice in one process,
    # reusing the same SQLAlchemy MetaData instance. Allow redefining the table safely.
    __table_args__ = (
        Index("ix_runrecord_cycle_start_time", "cycle", "start_time"),
        {"extend_existing": True},
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    status: RunStatus = Field(default=RunStatus.PENDING, index=True)
    start_time: datetime | None = Field(default=None, index=True)
    end_time: datetime | None = Field(default=None)
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", _METADATA_JSON_COL, nullable=False),
    )
    # Promoted from ``metadata`` (see promoted_columns); None on rows older than revision 0002
    # that the migration could not fill.
    cycle: str | None = Field(default=None, index=True, max_length=512)
    health_pct: float | None = Field(default=None)
    exit_code: int | None = Field(default=None)
    branch: str | None = Field(default=None, max_length=255)
    # ``commit`` is a reserved word in SQL, hence the suffix.
    commit_sha: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=32)
    snapshot_dir: str | None = Field(default=None, max_length=1024)


PROMOTED_COLUMNS = (
    "cycle",
    "health_pct",
    "exit_code",
    "branch",
    "commit",
    "source",
    "snapshot_dir",
)


def _text(value: Any, limit: int) -> str | None:
    return str(value)[:limit] if value else None


def promoted_columns(metadata: dict[str, Any]) -> dict[str, Any]:
    """The column values that ``metadata`` implies, with the same precedence the views use.

    Shared by the repository (on every write) and by migration 0002 (backfill), so
    a run looks the same whether it was recorded before or after the columns existed.
    """
    health = metadata.get("health_pct")
    exit_code = metadata.get("exit_code")
    return {
        "cycle": _text(metadata.get("plan"), 512),
        "health_pct": float(health) if health is not None else None,
        "exit_code": int(exit_code) if exit_code is not None else None,
        "branch": _text(metadata.get("ci_ref_name") or metadata.get("git_branch"), 255),
        "commit_sha": _text(metadata.get("ci_commit_sha") or metadata.get("git_commit"), 64),
        "source": _text(metadata.get("source"), 32),
        "snapshot_dir": _text(metadata.get("snapshot_dir"), 1024),
    }


def sync_promoted_columns(record: RunRecord) -> None:
    """Copy the promoted values out of ``record.metadata_`` onto its columns."""
    for name, value in promoted_columns(record.metadata_ or {}).items():
        setattr(record, name, value)


class TestResult(SQLModel, table=True):
    """One test's outcome in one run: the row behind flaky detection, per-test history and failures.

    Written when a run is recorded (``DbBackend``) from the run's Allure results,
    and once, on first read, for runs recorded before this table existed. The
    columns are bounded copies of what :func:`qasight.reporting.allure_cases.load_cases`
    returns; attachments and traces stay in the run's snapshot files.
    """

    __tablename__ = "test_result"
    __table_args__ = (
        Index("ix_test_result_run_id", "run_id"),
        {"extend_existing": True},
    )

    run_id: str = Field(primary_key=True, max_length=64)
    key: str = Field(primary_key=True, max_length=200)
    name: str = Field(default="", max_length=120)
    full_name: str = Field(default="", max_length=1024)
    group: str = Field(default="", max_length=100)
    stage: str = Field(default="", max_length=128)
    framework: str = Field(default="", max_length=32)
    status: str = Field(default="unknown", max_length=16)
    duration_ms: int | None = Field(default=None)
    message: str = Field(default="", max_length=300)
    # "stack_overflow" | "crash" for a failure that shows one (engine/crash_signatures.py).
    crash: str = Field(default="", max_length=16)
    # Failed, then passed on a retry inside this run (JUnit reruns, Surefire flakyFailure).
    retried: bool = Field(default=False)
    # What the test is about, [{name, value}] (feature, severity, tag...), and its issue
    # links, [{name, url, type}]. NULL on rows written before revision 0006.
    labels: list[dict[str, str]] | None = Field(
        default=None, sa_column=Column("labels", _METADATA_JSON_COL, nullable=True)
    )
    links: list[dict[str, str]] | None = Field(
        default=None, sa_column=Column("links", _METADATA_JSON_COL, nullable=True)
    )


class ReportArchive(SQLModel, table=True):
    """Stored Allure/JSON artifacts for a completed CLI cycle (zip payload).

    Denormalized counters support fast listing/filtering without unpacking ``artifact_bytes``.
    Existing deployments that created this table before these columns need a manual
    ``ALTER TABLE`` (Postgres/MySQL) or a fresh SQLite file for dev.
    """

    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    cycle_name: str = Field(index=True, max_length=512)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC), index=True)
    exit_code: int = Field(default=0)
    summary_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("summary_json", _METADATA_JSON_COL, nullable=False),
    )
    artifact_bytes: bytes = Field(sa_column=Column("artifact_bytes", LargeBinary, nullable=False))
    total_tests: int | None = Field(default=None, index=True)
    passed: int | None = Field(default=None)
    failed: int | None = Field(default=None)
    broken: int | None = Field(default=None)
    skipped: int | None = Field(default=None)
    unknown: int | None = Field(
        default=None,
        description="Sum of Allure unknown-status counts across per-stage result trees.",
    )
    allure_duration_ms: int | None = Field(
        default=None,
        description="Sum of per-result-tree duration spans from *-result.json timestamps.",
    )
    plan_duration_ms: int | None = Field(
        default=None,
        index=True,
        description="Wall time from plan_result.json duration_s when present.",
    )


class QueuedRun(SQLModel, table=True):
    """An API run waiting for a worker, kept only when ``TESTO_RUN_QUEUE=db``.

    The row is deleted when a worker starts the run, so the table only ever holds
    runs that have not started. ``request_json`` is ``qasight_api.run_queue.RunRequest``.
    """

    __tablename__ = "queued_run"
    __table_args__ = {"extend_existing": True}

    id: str = Field(primary_key=True, max_length=64)
    cycle: str = Field(index=True, max_length=512)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC), index=True)
    request_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("request_json", _METADATA_JSON_COL, nullable=False),
    )


class UserAccount(SQLModel, table=True):
    """A team server account, used only when ``TESTO_AUTH=accounts``.

    ``password_hash`` is ``scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>``. Bumping
    ``session_version`` signs the user out of every browser.
    """

    __tablename__ = "user_account"
    __table_args__ = {"extend_existing": True}

    username: str = Field(primary_key=True, max_length=64)
    password_hash: str = Field(max_length=512)
    role: str = Field(default="viewer", max_length=16)
    session_version: int = Field(default=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class AuditEvent(SQLModel, table=True):
    """One write to the API by a signed-in user (``TESTO_AUTH=accounts``)."""

    __tablename__ = "audit_event"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC), index=True)
    username: str = Field(index=True, max_length=64)
    method: str = Field(max_length=8)
    path: str = Field(max_length=1024)
    status: int = Field(default=0)


class FailureTriage(SQLModel, table=True):
    """Why a failure happens, marked once per cycle and failure message.

    ``signature`` is :func:`qasight.services.run_failures.message_signature`
    of the message, so later failures with the same message (numbers, ids and
    quoted values aside) inherit the cause. See :mod:`qasight.services.triage`.
    """

    __tablename__ = "failure_triage"
    __table_args__ = {"extend_existing": True}

    # sha256 of "<cycle>\n<signature>", so a cycle has one cause per message.
    id: str = Field(primary_key=True, max_length=64)
    cycle: str = Field(index=True, max_length=512)
    signature: str = Field(max_length=1024)
    cause: str = Field(max_length=32)
    note: str = Field(default="", max_length=500)
    # The failure it was marked on, so the UI can say where the cause came from.
    example_message: str = Field(default="", max_length=1000)
    run_id: str = Field(default="", max_length=64)
    test_key: str = Field(default="", max_length=200)
    marked_by: str | None = Field(default=None, max_length=64)
    marked_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class QuarantinedTest(SQLModel, table=True):
    """A test whose failures don't fail its cycle's runs, set from the UI, API or CLI.

    Added to the cycle's ``quarantine:`` rules from ``qasight.yaml`` at run
    time; see :mod:`qasight.services.quarantine_store`.
    """

    __tablename__ = "quarantined_test"
    __table_args__ = {"extend_existing": True}

    # sha256 of "<cycle>\n<test>", so a cycle lists a test (or glob) once.
    id: str = Field(primary_key=True, max_length=64)
    cycle: str = Field(index=True, max_length=512)
    # An exact test name (or full name), or an ``fnmatch`` glob.
    test: str = Field(max_length=1024)
    reason: str = Field(default="", max_length=500)
    owner: str = Field(default="", max_length=200)
    added_by: str | None = Field(default=None, max_length=64)
    added_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class ResourceItemRow(SQLModel, table=True):
    """A key of a resource pool, as last described by a client's ``resources:`` config.

    Pools live in each client's config, so this table is how the server (and the
    Resources page) learns them. ``disabled`` takes a broken key out of the pool
    until someone puts it back. See :mod:`qasight.resources.store`.
    """

    __tablename__ = "resource_item"
    __table_args__ = {"extend_existing": True}

    pool: str = Field(primary_key=True, max_length=128)
    item: str = Field(primary_key=True, max_length=128)
    labels: list[str] = Field(
        default_factory=list, sa_column=Column("labels", _METADATA_JSON_COL, nullable=False)
    )
    data: dict[str, str] = Field(
        default_factory=dict, sa_column=Column("data", _METADATA_JSON_COL, nullable=False)
    )
    disabled: bool = Field(default=False)
    disabled_reason: str = Field(default="", max_length=500)
    disabled_by: str | None = Field(default=None, max_length=64)
    # Epoch seconds, like the lease times: comparable on every dialect.
    seen_at: float = Field(default=0.0)
    # The last lease ended with a successful on_release (or ``release --clean``),
    # so the key is known to be reset. Cleared when someone takes it.
    clean: bool = Field(default=False)


class ResourceLease(SQLModel, table=True):
    """Who holds a pool's key right now. One row per key, so taking one is a single insert.

    The primary key is the key itself, which is what makes two holders impossible:
    a second insert for the same key fails on every database, across processes and
    machines. ``expires_at`` moves forward with each heartbeat; a row past
    ``expires_at + reuse_delay_s`` is deleted by the next take.
    """

    __tablename__ = "resource_lease"
    __table_args__ = {"extend_existing": True}

    pool: str = Field(primary_key=True, max_length=128)
    item: str = Field(primary_key=True, max_length=128)
    lease_id: str = Field(unique=True, index=True, max_length=64)
    holder_user: str = Field(default="", max_length=64)
    holder_host: str = Field(default="", max_length=255)
    holder_pid: int = Field(default=0)
    run_id: str = Field(default="", max_length=128)
    stage: str = Field(default="", max_length=128)
    worker: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=200)
    taken_at: float = Field(default=0.0)
    heartbeat_at: float = Field(default=0.0)
    expires_at: float = Field(default=0.0, index=True)
    ttl_s: float = Field(default=120.0)
    reuse_delay_s: float = Field(default=0.0)


class ResourceAction(SQLModel, table=True):
    """One provider command a person ran with ``qasight pool add/remove/sweep --apply``.

    QASight never runs ``create`` or ``destroy`` on its own; this log says who
    did, from which machine, and how it went. Dry runs are not logged.
    """

    __tablename__ = "resource_action"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    at: float = Field(default=0.0, index=True)
    pool: str = Field(index=True, max_length=128)
    item: str = Field(max_length=128)
    verb: str = Field(max_length=16)  # create | destroy
    user: str = Field(default="", max_length=64)
    host: str = Field(default="", max_length=255)
    command: str = Field(default="", max_length=500)
    ok: bool = Field(default=False)
    detail: str = Field(default="", max_length=500)


class ResourceWaiter(SQLModel, table=True):
    """A request waiting for a key, so keys go out first come, first served.

    ``candidates`` are the keys it would accept; a waiter only lets a later one
    go first when they want different keys. Rows expire when the waiter stops
    asking (see ``WAITER_TTL_S``).
    """

    __tablename__ = "resource_waiter"
    __table_args__ = {"extend_existing": True}

    id: str = Field(primary_key=True, max_length=64)
    pool: str = Field(index=True, max_length=128)
    candidates: list[str] = Field(
        default_factory=list, sa_column=Column("candidates", _METADATA_JSON_COL, nullable=False)
    )
    holder_user: str = Field(default="", max_length=64)
    created_at: float = Field(default=0.0, index=True)
    heartbeat_at: float = Field(default=0.0)


class ScorecardResult(SQLModel, table=True):
    """One tool's score for one version of a project, on the release scorecard.

    One row per (project, version, tool): a newer report for the same three
    replaces it. See :mod:`qasight.services.scorecard`.
    """

    __tablename__ = "scorecard_result"
    __table_args__ = (
        Index("ix_scorecard_result_project", "project"),
        {"extend_existing": True},
    )

    # sha256 of "<project>\n<version>\n<tool>".
    id: str = Field(primary_key=True, max_length=64)
    project: str = Field(max_length=255)
    version: str = Field(max_length=128)
    tool: str = Field(max_length=64)
    score: int = Field(default=0)
    summary: str = Field(default="", max_length=500)
    # {"metrics": {...}, "counts": {...}, "findings": [{severity, title, detail, location}]}
    details: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column("details", _METADATA_JSON_COL, nullable=False)
    )
    commit_sha: str | None = Field(default=None, max_length=64)
    # "scan" | "import" | "server"
    source: str = Field(default="import", max_length=32)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class ScorecardToolSetting(SQLModel, table=True):
    """A scorecard tool switched on or off; tools without a row use their default."""

    __tablename__ = "scorecard_tool"
    __table_args__ = {"extend_existing": True}

    tool: str = Field(primary_key=True, max_length=64)
    enabled: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
