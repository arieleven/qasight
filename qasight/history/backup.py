"""Back up and restore the run history: every table, plus the per-run report snapshots.

A backup is one ``.tar.gz``:

- ``testo-backup.json``: format version, when, which ``qasight``, the source
  database dialect and the row count of each table;
- ``tables/<table>.jsonl``: one JSON object per row, read through SQLAlchemy, so a
  backup taken from SQLite restores into Postgres or MySQL and the other way round;
- ``files/history/...``: ``static/history/<run_id>/`` (each run's results, used by
  Compare, flaky detection and the test page).

Restore refuses a database that already has runs unless asked to replace them, and
only writes the columns today's tables still have, so an older backup restores into
a newer schema. The AI settings file (it holds API keys) is never included; the
``user_account`` table holds password hashes, so keep backups as private as the
database itself.
"""

from __future__ import annotations

import base64
import enum
import io
import json
import os
import tarfile
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import JSON, Table, delete, func, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.types import TypeEngine

FORMAT = 1
MANIFEST = "testo-backup.json"
_TABLES_DIR = "tables"
_FILES_DIR = "files/history"
_BATCH = 500


class BackupError(RuntimeError):
    """The backup file is unreadable, or restoring it would lose data."""


@dataclass(frozen=True)
class BackupSummary:
    path: Path
    dialect: str
    rows: dict[str, int]
    files: int


@dataclass(frozen=True)
class RestoreSummary:
    rows: dict[str, int]
    files: int
    skipped_tables: tuple[str, ...] = ()
    dropped_columns: dict[str, list[str]] = field(default_factory=dict)


def _tables() -> list[Table]:
    from sqlmodel import SQLModel

    from qasight.repository.db_config import create_db_and_tables

    create_db_and_tables()  # registers every model and makes sure the tables exist
    return list(SQLModel.metadata.sorted_tables)


def _is_json(kind: TypeEngine[Any]) -> bool:
    impl = getattr(kind, "impl", kind)
    return isinstance(kind, JSON) or isinstance(impl, JSON)


def _encode(value: Any, *, json_column: bool) -> Any:
    if value is None or json_column:
        return value
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$b64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, enum.Enum):
        return value.name
    return value


def _decode(value: Any, *, json_column: bool) -> Any:
    if json_column or not isinstance(value, dict) or len(value) != 1:
        return value
    ((tag, raw),) = value.items()
    if tag == "$dt":
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    if tag == "$date":
        return date.fromisoformat(raw)
    if tag == "$uuid":
        return uuid.UUID(raw)
    if tag == "$b64":
        return base64.b64decode(raw)
    return value


def _engine() -> Engine:
    from qasight.repository.db_config import get_engine

    return get_engine()


def _history_root() -> Path:
    from qasight import paths

    return paths.STATIC_HISTORY_ROOT


def _rows(engine: Engine, table: Table) -> Iterator[dict[str, Any]]:
    json_cols = {c.name for c in table.columns if _is_json(c.type)}
    with engine.connect() as conn:
        result = conn.execution_options(yield_per=_BATCH).execute(select(table))
        for row in result.mappings():
            yield {k: _encode(v, json_column=k in json_cols) for k, v in row.items()}


def default_backup_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(tz=UTC)).strftime("%Y%m%d-%H%M%S")
    return f"testo-backup-{stamp}.tar.gz"


def create_backup(out: Path, *, include_files: bool = True) -> BackupSummary:
    """Write every table (and, unless ``include_files`` is off, the run snapshots) to ``out``."""
    from qasight import __version__

    engine = _engine()
    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    files = 0
    partial = out.with_name(out.name + ".partial")
    with tempfile.TemporaryDirectory(prefix="testo-backup-") as scratch:
        with tarfile.open(partial, "w:gz") as tar:
            for table in _tables():
                dump = Path(scratch) / f"{table.name}.jsonl"
                n = 0
                with dump.open("w", encoding="utf-8") as fh:
                    for row in _rows(engine, table):
                        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                        n += 1
                counts[table.name] = n
                tar.add(dump, arcname=f"{_TABLES_DIR}/{table.name}.jsonl")
            history = _history_root()
            if include_files and history.is_dir():
                for path in sorted(history.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        rel = path.relative_to(history).as_posix()
                        tar.add(path, arcname=f"{_FILES_DIR}/{rel}", recursive=False)
                        files += 1
            manifest = {
                "format": FORMAT,
                "created_at": datetime.now(tz=UTC).isoformat(),
                "qasight": __version__,
                "dialect": engine.dialect.name,
                "tables": counts,
                "files": files,
            }
            blob = json.dumps(manifest, indent=2).encode("utf-8")
            info = tarfile.TarInfo(MANIFEST)
            info.size = len(blob)
            info.mtime = int(datetime.now(tz=UTC).timestamp())
            tar.addfile(info, io.BytesIO(blob))
    os.replace(partial, out)
    return BackupSummary(path=out, dialect=engine.dialect.name, rows=counts, files=files)


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        with tarfile.open(path, "r:gz") as tar:
            member = tar.getmember(MANIFEST)
            fh = tar.extractfile(member)
            manifest = json.loads(fh.read().decode("utf-8")) if fh else None
    except (OSError, KeyError, tarfile.TarError, ValueError) as exc:
        raise BackupError(f"{path} is not a QASight backup ({exc})") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BackupError(f"{path}: unsupported backup format {manifest!r:.80}")
    return manifest


def _safe_member_path(name: str) -> PurePosixPath | None:
    """The path under ``files/history/`` for a tar member, or ``None`` if it would escape it."""
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    try:
        return pure.relative_to(_FILES_DIR)
    except ValueError:
        return None


def existing_runs() -> int:
    engine = _engine()
    runs = next(t for t in _tables() if t.name == "runrecord")
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(runs)).scalar_one())


def restore_backup(path: Path, *, replace: bool = False) -> RestoreSummary:
    """Load ``path`` into the active database and ``static/history/``.

    Raises :class:`BackupError` when the database already has runs and ``replace`` is
    off; with ``replace`` every table in the backup is emptied first.
    """
    read_manifest(path)
    tables = {t.name: t for t in _tables()}
    engine = _engine()
    if not replace and existing_runs():
        raise BackupError(
            "this database already has runs; restore into an empty one, or pass --replace "
            "to delete them first"
        )
    rows: dict[str, int] = {}
    skipped: list[str] = []
    dropped: dict[str, list[str]] = {}
    files = 0
    history = _history_root()
    with tarfile.open(path, "r:gz") as tar:
        members = tar.getmembers()
        dumps = {
            PurePosixPath(m.name).stem: m
            for m in members
            if m.isfile() and m.name.startswith(f"{_TABLES_DIR}/") and m.name.endswith(".jsonl")
        }
        ordered = [t for t in tables.values() if t.name in dumps]
        skipped = sorted(set(dumps) - set(tables))
        with engine.begin() as conn:
            if replace:
                for table in reversed(ordered):
                    conn.execute(delete(table))
            for table in ordered:
                columns = {c.name: c for c in table.columns}
                json_cols = {n for n, c in columns.items() if _is_json(c.type)}
                fh = tar.extractfile(dumps[table.name])
                if fh is None:
                    continue
                batch: list[dict[str, Any]] = []
                n = 0
                for line in io.TextIOWrapper(fh, encoding="utf-8"):
                    if not line.strip():
                        continue
                    raw = json.loads(line)
                    extra = sorted(set(raw) - set(columns))
                    if extra:
                        known = dropped.setdefault(table.name, [])
                        known.extend(c for c in extra if c not in known)
                    batch.append(
                        {
                            k: _decode(v, json_column=k in json_cols)
                            for k, v in raw.items()
                            if k in columns
                        }
                    )
                    if len(batch) >= _BATCH:
                        conn.execute(insert(table), batch)
                        n += len(batch)
                        batch = []
                if batch:
                    conn.execute(insert(table), batch)
                    n += len(batch)
                rows[table.name] = n
        for member in members:
            rel = _safe_member_path(member.name)
            if rel is None or not member.isfile():
                continue
            target = history / Path(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with target.open("wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            files += 1
    return RestoreSummary(
        rows=rows, files=files, skipped_tables=tuple(skipped), dropped_columns=dropped
    )
