"""``qasight backup`` / ``qasight restore`` (qasight/history/backup.py) on file-backed SQLite."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.history.backup import BackupError, create_backup, read_manifest, restore_backup
from qasight.repository.db import get_repository, reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight.repository.models import RunStatus


def _use_db(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    reset_repository_cache()
    reset_engine_cache()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")


@pytest.fixture
def history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "static" / "history"
    monkeypatch.setattr("qasight.paths.STATIC_HISTORY_ROOT", root)
    yield root
    reset_repository_cache()
    reset_engine_cache()


def _seed(history: Path) -> str:
    repo = get_repository()
    record = repo.create_run(
        status=RunStatus.FAILED,
        metadata={"plan": "web", "returncode": 1, "nested": {"$dt": "not a tag here"}},
    )
    run_dir = history / str(record.id) / "artifacts"
    run_dir.mkdir(parents=True)
    (run_dir / "1-result.json").write_text('{"status": "failed"}', encoding="utf-8")
    return str(record.id)


def test_backup_restores_into_an_empty_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, history: Path
) -> None:
    _use_db(monkeypatch, tmp_path / "old.db")
    run_id = _seed(history)
    out = tmp_path / "b.tar.gz"
    summary = create_backup(out)
    assert summary.rows["runrecord"] == 1 and summary.files == 1
    manifest = read_manifest(out)
    assert manifest["dialect"] == "sqlite" and manifest["tables"]["runrecord"] == 1

    # A fresh database and an empty history folder: the run and its files come back.
    for path in history.rglob("*"):
        if path.is_file():
            path.unlink()
    _use_db(monkeypatch, tmp_path / "new.db")
    restored = restore_backup(out)
    assert restored.rows["runrecord"] == 1 and restored.files == 1
    record = get_repository().get_run(run_id)
    assert record is not None
    assert record.status == RunStatus.FAILED
    # JSON columns keep their own dicts, even one that looks like a tag.
    assert record.metadata_["nested"] == {"$dt": "not a tag here"}
    assert record.start_time is None or record.start_time.tzinfo is not None
    assert (history / run_id / "artifacts" / "1-result.json").is_file()


def test_restore_refuses_a_database_with_runs_unless_replacing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, history: Path
) -> None:
    _use_db(monkeypatch, tmp_path / "one.db")
    _seed(history)
    out = tmp_path / "b.tar.gz"
    create_backup(out, include_files=False)
    with pytest.raises(BackupError, match="already has runs"):
        restore_backup(out)
    restored = restore_backup(out, replace=True)
    assert restored.rows["runrecord"] == 1
    assert len(get_repository().list_recent_runs(limit=10)) == 1


def test_restore_ignores_paths_that_escape_and_unknown_columns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, history: Path
) -> None:
    _use_db(monkeypatch, tmp_path / "src.db")
    _seed(history)
    good = tmp_path / "good.tar.gz"
    create_backup(good, include_files=False)
    # Add a member that tries to escape, and a column a future version might have.
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(good, "r:gz") as src, tarfile.open(evil, "w:gz") as dst:
        for member in src.getmembers():
            data = src.extractfile(member)
            if member.name == "tables/runrecord.jsonl" and data is not None:
                rows = [json.loads(line) for line in data.read().decode().splitlines()]
                for row in rows:
                    row["from_the_future"] = 1
                blob = "".join(json.dumps(r) + "\n" for r in rows).encode()
                member.size = len(blob)
                dst.addfile(member, io.BytesIO(blob))
            else:
                dst.addfile(member, data)
        payload = b"pwned"
        info = tarfile.TarInfo("files/history/../../escape.txt")
        info.size = len(payload)
        dst.addfile(info, io.BytesIO(payload))
    _use_db(monkeypatch, tmp_path / "dst.db")
    restored = restore_backup(evil)
    assert restored.files == 0
    assert restored.dropped_columns == {"runrecord": ["from_the_future"]}
    assert not (tmp_path / "escape.txt").exists()


def test_a_file_that_is_not_a_backup_is_refused(tmp_path: Path) -> None:
    junk = tmp_path / "x.tar.gz"
    junk.write_bytes(b"nope")
    with pytest.raises(BackupError):
        read_manifest(junk)


def test_cli_backup_then_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, history: Path
) -> None:
    _use_db(monkeypatch, tmp_path / "cli.db")
    _seed(history)
    out = tmp_path / "cli.tar.gz"
    runner = CliRunner()
    result = runner.invoke(app, ["backup", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Backed up 1 runs" in result.output

    result = runner.invoke(app, ["restore", str(out)])
    assert result.exit_code == 2, result.output
    assert "already has runs" in result.output

    result = runner.invoke(app, ["restore", str(out), "--replace", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Restored 1 runs" in result.output
