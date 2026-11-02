"""Storage limits: env overrides for history, report archive retention, and the file size cap."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testo_core import paths
from testo_core.cli.app import app
from testo_core.config.errors import ConfigValidationError
from testo_core.config.history_section import (
    HistoryConfig,
    HistoryRetention,
    parse_history,
    with_env_overrides,
)
from testo_core.history import retention
from testo_core.repository.db import get_report_archive_repository, get_repository
from testo_core.repository.models import RunStatus

pytestmark = pytest.mark.tier_fast


# --- env overrides -------------------------------------------------------------


def test_env_fills_in_limits_when_there_is_no_file() -> None:
    got = with_env_overrides(
        HistoryConfig(),
        {"TESTO_RETENTION_DAYS": "180", "TESTO_HISTORY_MAX_FILE_MB": "100"},
    )
    assert got.retention == HistoryRetention(days=180)
    assert got.max_file_mb == 100


def test_env_wins_over_the_file_and_zero_switches_a_limit_off() -> None:
    file = HistoryConfig(retention=HistoryRetention(days=90, runs_per_cycle=200), max_file_mb=5)
    got = with_env_overrides(
        file, {"TESTO_RETENTION_DAYS": "0", "TESTO_RETENTION_RUNS_PER_CYCLE": "50"}
    )
    assert got.retention == HistoryRetention(days=None, runs_per_cycle=50)
    assert got.max_file_mb == 5
    assert with_env_overrides(file, {"TESTO_RETENTION_DAYS": ""}) == file


def test_bad_env_values_are_refused() -> None:
    with pytest.raises(ConfigValidationError, match="TESTO_RETENTION_DAYS"):
        with_env_overrides(HistoryConfig(), {"TESTO_RETENTION_DAYS": "lots"})
    with pytest.raises(ConfigValidationError):
        with_env_overrides(HistoryConfig(), {"TESTO_RETENTION_DAYS": "-1"})


def test_max_file_mb_in_the_file() -> None:
    assert parse_history({"max_file_mb": 20}).max_file_mb == 20
    with pytest.raises(ConfigValidationError, match="max_file_mb"):
        parse_history({"max_file_mb": 0})


# --- report archives -------------------------------------------------------------


def _archive(cycle: str, days_ago: int) -> str:
    repo = get_report_archive_repository()
    row = repo.insert(cycle_name=cycle, exit_code=0, summary_json={}, artifact_bytes=b"zip")
    from sqlmodel import Session

    from testo_core.repository.db_config import get_engine
    from testo_core.repository.models import ReportArchive

    with Session(get_engine()) as session:
        stored = session.get(ReportArchive, row.id)
        assert stored is not None
        stored.created_at = datetime.now(tz=UTC) - timedelta(days=days_ago)
        session.add(stored)
        session.commit()
    return str(row.id)


def test_report_archives_follow_retention() -> None:
    keep = _archive("web", 1)
    old = _archive("web", 40)
    dry = retention.prune_report_archives(HistoryRetention(days=30), dry_run=True)
    assert dry.archive_ids == (old,) and dry.archives_deleted == 0

    done = retention.prune_report_archives(HistoryRetention(days=30))
    assert done.archives_deleted == 1
    left = {k[0] for k in get_report_archive_repository().list_keys()}
    assert keep in left and old not in left


def test_report_archives_keep_the_newest_per_cycle() -> None:
    ids = [_archive("web", d) for d in (3, 2, 1)]
    _archive("api", 5)
    done = retention.prune_report_archives(HistoryRetention(runs_per_cycle=1))
    assert done.archives_deleted == 2
    left = {k[0] for k in get_report_archive_repository().list_keys()}
    assert ids[2] in left and ids[0] not in left and len(left) == 2


def test_testo_report_delete() -> None:
    target = _archive("web", 1)
    runner = CliRunner()
    refused = runner.invoke(app, ["report", "delete", "--id", target])
    assert refused.exit_code == 2
    done = runner.invoke(app, ["report", "delete", "--id", target, "--yes"])
    assert done.exit_code == 0, done.output
    assert "Deleted 1" in done.output
    missing = runner.invoke(app, ["report", "delete", "--id", str(uuid.uuid4()), "-y"])
    assert missing.exit_code == 2


def test_clean_history_uses_env_limits_and_prunes_archives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no testosterone.yaml here
    monkeypatch.setenv("TESTO_RETENTION_DAYS", "30")
    _archive("web", 40)
    result = CliRunner().invoke(app, ["clean", "--history", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Deleted 1 report archive" in result.output


def test_server_timer_runs_on_env_limits_without_a_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = pytest.importorskip("testo_api.main")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TESTO_RETENTION_DAYS", "30")
    seen: list[HistoryRetention] = []
    archived = threading.Event()

    def fake_prune(limits: HistoryRetention, *, dry_run: bool = False) -> retention.PruneReport:
        seen.append(limits)
        return retention.PruneReport()

    def fake_archives(
        limits: HistoryRetention, *, dry_run: bool = False
    ) -> retention.ArchivePruneReport:
        archived.set()
        return retention.ArchivePruneReport()

    monkeypatch.setattr(retention, "prune_history", fake_prune)
    monkeypatch.setattr(retention, "prune_report_archives", fake_archives)
    stop = main._start_retention_timer()
    try:
        assert archived.wait(5)
    finally:
        stop()
    assert seen == [HistoryRetention(days=30)]


# --- file size cap ------------------------------------------------------------------


def test_big_files_stay_out_of_the_history_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from testo_core.persistence.db_backend import DbBackend

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TESTO_HISTORY_MAX_FILE_MB", "1")
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", tmp_path / "static" / "history")
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    plan = tmp_path / "artifacts" / "web" / "unit"
    plan.mkdir(parents=True)
    (plan / "result.json").write_text("{}")
    (plan / "video.webm").write_bytes(b"0" * (2 * 1024 * 1024))
    (tmp_path / "artifacts" / "web" / ".lock").write_text("{}")

    record = get_repository().create_run(status=RunStatus.COMPLETED, metadata={"plan": "web"})
    backend = DbBackend(tmp_path / "artifacts")
    backend._record_run_snapshot(get_repository(), str(record.id), "web")

    copy = tmp_path / "static" / "history" / str(record.id) / "artifacts"
    assert (copy / "unit" / "result.json").is_file()
    assert not (copy / "unit" / "video.webm").exists()
    assert not (copy / ".lock").exists()
    md = get_repository().get_run(str(record.id)).metadata_  # type: ignore[union-attr]
    assert md["snapshot_skipped_files"] == ["unit/video.webm"]


def test_a_failed_copy_is_noted_and_cleaned_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import shutil

    from testo_core.persistence.db_backend import DbBackend

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", tmp_path / "static" / "history")
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    (tmp_path / "artifacts" / "web").mkdir(parents=True)

    def full_disk(src: Path, dst: Path, **_kw: object) -> None:
        Path(dst).mkdir(parents=True)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copytree", full_disk)
    record = get_repository().create_run(status=RunStatus.COMPLETED, metadata={"plan": "web"})
    DbBackend(tmp_path / "artifacts")._record_run_snapshot(get_repository(), str(record.id), "web")
    assert not (tmp_path / "static" / "history" / str(record.id)).exists()
    md = get_repository().get_run(str(record.id)).metadata_  # type: ignore[union-attr]
    assert "No space left" in md["snapshot_error"]
