"""P11-P14 from docs/engine-cli-review.md: run times, symlinked attachments, Windows Jest paths, XML whitespace."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from qasight.engine.exit_codes import EngineExitCode
from qasight.engine.result import PlanResult
from qasight.persistence.db_backend import DbBackend
from qasight.reporting.allure_passthrough import copy_allure_result
from qasight.reporting.jest_import import _relative_paths
from qasight.reporting.junit_import import import_junit_reports
from qasight.repository.db import get_repository, reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight.repository.models import RunStatus

pytestmark = pytest.mark.tier_fast


# P11 ---------------------------------------------------------------------------------


@patch("qasight.repository.db.get_repository")
def test_engine_run_records_its_real_start_and_end(
    mock_get_repo: MagicMock, tmp_path: Path
) -> None:
    repo = MagicMock()
    mock_get_repo.return_value = repo
    result = PlanResult(
        plan_name="smoke",
        started_at=1_700_000_000.0,
        finished_at=1_700_000_090.0,
        duration_s=90.0,
        stages=(),
        aggregate_returncode=0,
        exit_code=EngineExitCode.SUCCESS,
    )

    DbBackend(tmp_path).persist(result)

    kwargs = repo.create_run.call_args[1]
    assert kwargs["start_time"] == datetime.fromtimestamp(1_700_000_000.0, tz=UTC)
    assert kwargs["end_time"] == datetime.fromtimestamp(1_700_000_090.0, tz=UTC)


def test_create_run_keeps_given_times(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_repository_cache()
    reset_engine_cache()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    try:
        repo = get_repository()
        start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        end = datetime(2026, 1, 1, 12, 5, tzinfo=UTC)
        row = repo.create_run(status=RunStatus.COMPLETED, start_time=start, end_time=end)
        stored = repo.get_run(row.id)
        assert stored is not None
        assert stored.start_time is not None and stored.end_time is not None
        assert stored.start_time.replace(tzinfo=UTC) == start
        assert stored.end_time.replace(tzinfo=UTC) == end
    finally:
        reset_repository_cache()
        reset_engine_cache()


# P12 ---------------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_allure_attachment_symlink_out_of_the_folder_is_not_copied(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("do not copy")
    results = tmp_path / "allure-results"
    results.mkdir()
    (results / "a-attachment.txt").symlink_to(secret)
    (results / "b-attachment.txt").write_text("fine")
    out = tmp_path / "out"
    out.mkdir()
    data = {
        "name": "t",
        "status": "passed",
        "attachments": [{"source": "a-attachment.txt"}, {"source": "b-attachment.txt"}],
    }

    (results / "x-result.json").write_text(json.dumps(data))

    _, copied = copy_allure_result(results / "x-result.json", data, results_dir=out, tool="t")

    assert copied == 1
    # Copies are renamed to ``<uuid>-attachment<ext>`` (security review R-fixes).
    assert [p.read_text() for p in out.iterdir()] == ["fine"]


# P13 ---------------------------------------------------------------------------------


def test_windows_jest_paths_are_made_relative_like_posix_ones(tmp_path: Path) -> None:
    windows = _relative_paths(
        ["C:\\ci\\repo\\src\\a.test.js", "C:\\ci\\repo\\src\\b\\b.test.js"], tmp_path
    )
    posix = _relative_paths(["/ci/repo/src/a.test.js", "/ci/repo/src/b/b.test.js"], tmp_path)
    assert sorted(windows.values()) == sorted(posix.values()) == ["a.test.js", "b/b.test.js"]


def test_relative_jest_paths_are_unchanged(tmp_path: Path) -> None:
    assert _relative_paths(["src/a.test.js"], tmp_path) == {"src/a.test.js": "src/a.test.js"}


# P14 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", [b"\n", b"  \r\n", b"\xef\xbb\xbf\n"])
def test_whitespace_before_the_xml_declaration_is_read(tmp_path: Path, prefix: bytes) -> None:
    report = tmp_path / "junit.xml"
    report.write_bytes(
        prefix
        + b'<?xml version="1.0" encoding="UTF-8"?>\n'
        + b'<testsuite name="s" tests="1"><testcase classname="c" name="t"/></testsuite>\n'
    )

    result = import_junit_reports(
        target_repo=tmp_path, patterns=("junit.xml",), results_dir=tmp_path / "out"
    )

    assert result.tests == 1
    assert not result.errors


def test_utf16_report_with_leading_whitespace_is_read(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    text = (
        '\n<?xml version="1.0" encoding="utf-16"?>\n'
        '<testsuite name="s" tests="1"><testcase classname="c" name="t"/></testsuite>\n'
    )
    report.write_bytes(text.encode("utf-16"))

    result = import_junit_reports(
        target_repo=tmp_path, patterns=("junit.xml",), results_dir=tmp_path / "out"
    )

    assert result.tests == 1
    assert json.dumps(list(result.errors)) == "[]"
