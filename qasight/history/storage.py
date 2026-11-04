"""How much the run history takes: runs, report archives, files, database and free disk.

Shown on the Runs page (``GET /api/v1/storage``), by ``qasight doctor`` and used by
the API's readiness check, so a team sees the history grow before the disk fills.
Every number is best-effort: one that can't be measured (a database without a size
function, a folder that can't be read) is ``None``, never an error.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from qasight import paths

logger = logging.getLogger(__name__)

# Below this share of free disk the API's readiness check warns; below the hard
# floor it reports degraded (writes will start failing).
LOW_DISK_PCT_ENV = "TESTO_LOW_DISK_PCT"
DEFAULT_LOW_DISK_PCT = 10.0
FULL_DISK_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class DiskSpace:
    path: str
    total_bytes: int
    free_bytes: int

    @property
    def free_pct(self) -> float:
        return 100.0 * self.free_bytes / self.total_bytes if self.total_bytes else 0.0


@dataclass(frozen=True)
class StorageSummary:
    runs: int | None
    report_archives: int | None
    report_archive_bytes: int | None
    history_files_bytes: int | None
    database_bytes: int | None
    database_dialect: str | None
    disk: DiskSpace | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.disk is not None:
            data["disk"]["free_pct"] = round(self.disk.free_pct, 1)
        return data


def low_disk_pct() -> float:
    raw = os.environ.get(LOW_DISK_PCT_ENV, "").strip()
    try:
        return float(raw) if raw else DEFAULT_LOW_DISK_PCT
    except ValueError:
        return DEFAULT_LOW_DISK_PCT


def disk_space(path: Path | None = None) -> DiskSpace | None:
    """Free space on the disk that holds *path* (default: the data folder)."""
    target = (path or paths.ORCHESTRATOR_ROOT).expanduser()
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return None
    return DiskSpace(path=str(target), total_bytes=usage.total, free_bytes=usage.free)


def folder_bytes(root: Path) -> int | None:
    """Total size of the regular files under *root*; 0 when it doesn't exist."""
    if not root.is_dir():
        return 0
    total = 0
    try:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    st = os.lstat(os.path.join(dirpath, name))
                except OSError:
                    continue
                total += st.st_size
    except OSError:
        return None
    return total


def _count(table: str) -> int | None:
    from sqlalchemy import text

    from qasight.repository.db_config import get_engine

    try:
        with get_engine().connect() as conn:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())  # noqa: S608
    except Exception:  # noqa: BLE001 - a missing table or a DB error is "unknown"
        logger.debug("storage: could not count %s", table, exc_info=True)
        return None


def _archive_bytes() -> int | None:
    from sqlalchemy import text

    from qasight.repository.db_config import get_engine

    try:
        engine = get_engine()
        length = "octet_length" if engine.dialect.name == "postgresql" else "length"
        with engine.connect() as conn:
            value = conn.execute(
                text(f"SELECT COALESCE(SUM({length}(artifact_bytes)), 0) FROM reportarchive")  # noqa: S608
            ).scalar_one()
            return int(value or 0)
    except Exception:  # noqa: BLE001
        logger.debug("storage: could not size report archives", exc_info=True)
        return None


def database_size() -> tuple[int | None, str | None]:
    """``(bytes, dialect)`` of the history database, when the dialect can say."""
    from sqlalchemy import text

    from qasight.repository.db_config import get_engine

    try:
        engine = get_engine()
    except Exception:  # noqa: BLE001
        return None, None
    dialect = engine.dialect.name
    try:
        if dialect == "sqlite":
            db_path = engine.url.database
            if not db_path or db_path == ":memory:":
                return None, dialect
            files = [Path(db_path + suffix) for suffix in ("", "-wal", "-shm")]
            return sum(f.stat().st_size for f in files if f.is_file()), dialect
        with engine.connect() as conn:
            if dialect == "postgresql":
                size = conn.execute(text("SELECT pg_database_size(current_database())"))
                return int(size.scalar_one()), dialect
            if dialect in ("mysql", "mariadb"):
                size = conn.execute(
                    text(
                        "SELECT COALESCE(SUM(data_length + index_length), 0) "
                        "FROM information_schema.tables WHERE table_schema = DATABASE()"
                    )
                )
                return int(size.scalar_one()), dialect
    except Exception:  # noqa: BLE001
        logger.debug("storage: could not size the database", exc_info=True)
    return None, dialect


def storage_summary() -> StorageSummary:
    """Every number at once. Walks ``static/history/``, so callers cache it."""
    db_bytes, dialect = database_size()
    return StorageSummary(
        runs=_count("runrecord"),
        report_archives=_count("reportarchive"),
        report_archive_bytes=_archive_bytes(),
        history_files_bytes=folder_bytes(paths.STATIC_HISTORY_ROOT),
        database_bytes=db_bytes,
        database_dialect=dialect,
        disk=disk_space(),
    )


def human_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
