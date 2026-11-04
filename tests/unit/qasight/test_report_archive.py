"""Tests for cycle zip build/extract and report archive repository."""

from __future__ import annotations

import json
import uuid
import zipfile
from pathlib import Path

import pytest

from qasight.repository.db import get_report_archive_repository, reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight.repository.report_archive_repository import SQLReportArchiveRepository
from qasight.services.report_archive import (
    aggregate_cycle_metrics,
    build_cycle_zip_bytes,
    extract_archive_to_plan_dir,
)


@pytest.fixture
def sqlite_report_repo(monkeypatch: pytest.MonkeyPatch) -> SQLReportArchiveRepository:
    reset_repository_cache()
    reset_engine_cache()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    try:
        yield get_report_archive_repository()
    finally:
        reset_repository_cache()
        reset_engine_cache()


def test_build_and_extract_cycle_zip_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    plan = "my-cycle"
    plan_dir = root / plan
    plan_dir.mkdir(parents=True)
    (plan_dir / "events.ndjson").write_text('{"event":"plan_started"}\n', encoding="utf-8")
    (plan_dir / "plan_result.json").write_text(
        json.dumps({"plan": plan, "exit_code": 3, "stages": []}),
        encoding="utf-8",
    )
    st = plan_dir / "stage-a"
    (st / "allure-results" / "pytest").mkdir(parents=True)
    (st / "allure-results" / "pytest" / "x-result.json").write_text("{}", encoding="utf-8")

    blob, summary, ec = build_cycle_zip_bytes(root, plan)
    assert ec == 3
    assert summary.get("plan") == plan

    out_root = tmp_path / "out-artifacts"
    extract_archive_to_plan_dir(zip_bytes=blob, dest_artifacts_root=out_root, plan_name=plan)
    assert (out_root / plan / "plan_result.json").is_file()
    assert (out_root / plan / "stage-a" / "allure-results" / "pytest" / "x-result.json").is_file()


def test_extract_archive_rejects_plan_name_outside_artifacts_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="plan name escapes root"):
        extract_archive_to_plan_dir(
            zip_bytes=b"not reached",
            dest_artifacts_root=tmp_path / "out-artifacts",
            plan_name="../outside",
        )


def test_extract_archive_rejects_zip_member_outside_plan_dir(tmp_path: Path) -> None:
    blob = tmp_path / "bad.zip"
    with zipfile.ZipFile(blob, "w") as zf:
        zf.writestr("../escape.txt", "owned")

    out_root = tmp_path / "out-artifacts"
    with pytest.raises(ValueError, match="archive member escapes destination"):
        extract_archive_to_plan_dir(
            zip_bytes=blob.read_bytes(),
            dest_artifacts_root=out_root,
            plan_name="safe-cycle",
        )

    assert not (out_root / "escape.txt").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_report_archive_repository_crud(sqlite_report_repo: SQLReportArchiveRepository) -> None:
    row = sqlite_report_repo.insert(
        cycle_name="c1",
        exit_code=0,
        summary_json={"plan": "c1"},
        artifact_bytes=b"ZIPBYTES",
    )
    assert row.id is not None
    got = sqlite_report_repo.get(row.id)
    assert got is not None
    assert got.artifact_bytes == b"ZIPBYTES"

    listed = sqlite_report_repo.list_recent(limit=10)
    assert len(listed) >= 1
    assert listed[0].cycle_name == "c1"

    assert sqlite_report_repo.get(uuid.uuid4()) is None
    assert sqlite_report_repo.get("not-a-uuid") is None


def test_aggregate_cycle_metrics_sums_stages(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    plan = "p1"
    plan_dir = root / plan
    (plan_dir / "s1" / "allure-results" / "pytest").mkdir(parents=True)
    (plan_dir / "s1" / "allure-results" / "pytest" / "a-result.json").write_text(
        '{"status":"passed","start":1000,"stop":2000}', encoding="utf-8"
    )
    (plan_dir / "s2" / "allure-results" / "pytest").mkdir(parents=True)
    (plan_dir / "s2" / "allure-results" / "pytest" / "b-result.json").write_text(
        '{"status":"failed","start":500,"stop":1500}', encoding="utf-8"
    )
    (plan_dir / "plan_result.json").write_text(
        '{"plan":"p1","exit_code":1,"duration_s": 42.5}', encoding="utf-8"
    )
    m = aggregate_cycle_metrics(plan_dir)
    assert m["total_tests"] == 2
    assert m["passed"] == 1
    assert m["failed"] == 1
    assert m["plan_duration_ms"] == 42500
    assert m["allure_duration_ms"] == 1000 + 1000
