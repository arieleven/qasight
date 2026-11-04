"""Every run-metadata key the read side uses must be written by persistence.

``history/views.py`` reads ``RunRecord.metadata_`` by string key, and a key that
persistence never writes reads as ``None`` forever without any error: Compare's
"Test time (sum)" and "Avg per test" showed "n/a" on every run that way. This
test persists a real run through :class:`DbBackend` into SQLite and checks the
stored keys against the keys ``views.py`` reads.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from qasight.engine.exit_codes import EngineExitCode
from qasight.engine.result import PlanResult, StageResult
from qasight.history import views
from qasight.history.views import view_from_record
from qasight.persistence.db_backend import DbBackend
from qasight.repository.db import get_repository, reset_repository_cache
from qasight.repository.db_config import reset_engine_cache
from qasight.repository.models import RunRecord, RunStatus

# Read for runs from before v1.0's engine; nothing writes it today, so it reads as None.
LEGACY_KEYS = {"audit_json"}
# Written only in CI (CI provenance) or from a git checkout outside CI; see the provenance tests below.
CONDITIONAL_KEYS = {
    "ci_ref_name",
    "ci_commit_sha",
    "ci_base_ref",
    "git_branch",
    "git_commit",
    "repository",
    # Only when the run was given --coverage (tests/unit/qasight/test_coverage_import.py).
    "coverage_pct",
    "coverage_covered",
    "coverage_total",
}


def _keys_read_by_views() -> set[str]:
    """String keys ``views.py`` reads from ``md``: ``md["k"]``, ``md.get("k")``, ``_optional_*(md, "k")``."""
    tree = ast.parse(Path(views.__file__).read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _is_md(node.value):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                keys.add(node.slice.value)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get" and _is_md(func.value):
                args = node.args
            elif len(node.args) == 2 and _is_md(node.args[0]):
                args = node.args[1:]
            else:
                continue
            if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                keys.add(args[0].value)
    return keys


def _is_md(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "md"


@pytest.fixture
def sqlite_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    reset_repository_cache()
    reset_engine_cache()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'runs.db'}")
    monkeypatch.setattr("qasight.paths.STATIC_HISTORY_ROOT", tmp_path / "static_history")
    yield
    reset_repository_cache()
    reset_engine_cache()


def _persisted_record(artifacts_root: Path) -> RunRecord:
    stage_dir = artifacts_root / "smoke" / "api"
    results_dir = stage_dir / "allure-results" / "pytest"
    results_dir.mkdir(parents=True)
    for name, status, ms in [("t1", "passed", 100), ("t2", "failed", 300)]:
        payload = {"name": name, "status": status, "start": 1_000_000, "stop": 1_000_000 + ms}
        (results_dir / f"{name}-result.json").write_text(json.dumps(payload), encoding="utf-8")
    stage = StageResult(
        stage_name="api",
        framework="pytest",
        returncode=1,
        started_at=1000.0,
        finished_at=1001.0,
        duration_s=1.0,
        log_path=None,
        artifacts_dir=stage_dir,
        command=("pytest",),
        output_tail="1 failed",
        timed_out=False,
        tier="unit",
    )
    result = PlanResult(
        plan_name="smoke",
        started_at=1000.0,
        finished_at=1001.0,
        duration_s=1.0,
        stages=(stage,),
        aggregate_returncode=1,
        exit_code=EngineExitCode.DOMAIN_FAILURE,
        target_repo=artifacts_root.parent,
    )
    run_id = DbBackend(artifacts_root).persist(result)
    assert run_id is not None
    record = get_repository().get_run(run_id)
    assert record is not None
    return record


@pytest.mark.contract
def test_views_read_keys_are_found_by_the_ast_scan() -> None:
    """Guards the scan itself: if views.py changes how it reads keys, this fails first."""
    read = _keys_read_by_views()
    assert {"run_id", "duration_s", "metrics_duration_ms", "avg_case_ms", "stages"} <= read


@pytest.mark.contract
def test_every_key_views_reads_is_written_by_persistence(sqlite_repo: None, tmp_path: Path) -> None:
    written = set((_persisted_record(tmp_path / "artifacts").metadata_ or {}).keys())

    missing = _keys_read_by_views() - written - LEGACY_KEYS - CONDITIONAL_KEYS

    assert not missing, (
        f"history/views.py reads {sorted(missing)} from run metadata, but DbBackend never "
        "writes them, so they always read as None. Write them in qasight/persistence/, "
        "or add them to LEGACY_KEYS with a reason."
    )


@pytest.mark.contract
def test_persisted_run_has_test_time_in_its_view(sqlite_repo: None, tmp_path: Path) -> None:
    view = view_from_record(_persisted_record(tmp_path / "artifacts"))

    assert view is not None
    assert view.metrics_duration_ms == 400
    assert view.avg_case_ms == pytest.approx(200.0)


@pytest.mark.contract
def test_persisted_run_has_target_repo_in_its_view(sqlite_repo: None, tmp_path: Path) -> None:
    view = view_from_record(_persisted_record(tmp_path / "artifacts"))

    assert view is not None
    assert view.target_repo == str(tmp_path)


@pytest.mark.contract
def test_run_saved_before_test_time_existed_reads_as_unknown() -> None:
    legacy = RunRecord(
        status=RunStatus.COMPLETED,
        metadata_={"run_id": "old", "duration_s": 2.0, "total_tests": 3, "passed": 3},
    )

    view = view_from_record(legacy)

    assert view is not None
    assert view.metrics_duration_ms is None
    assert view.avg_case_ms is None


@pytest.mark.contract
def test_run_saved_before_target_repo_existed_reads_as_unknown() -> None:
    legacy = RunRecord(status=RunStatus.COMPLETED, metadata_={"run_id": "old"})

    view = view_from_record(legacy)

    assert view is not None
    assert view.target_repo is None


@pytest.mark.contract
def test_pull_request_run_has_its_branch_and_target_in_its_view(
    sqlite_repo: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_SHA": "abc123",
        "GITHUB_REF_NAME": "7/merge",
        "GITHUB_HEAD_REF": "feature",
        "GITHUB_BASE_REF": "main",
        "GITHUB_REPOSITORY": "acme/shop",
    }.items():
        monkeypatch.setenv(name, value)
    view = view_from_record(_persisted_record(tmp_path / "artifacts"))
    assert view is not None
    assert (view.branch, view.commit, view.base_branch) == ("feature", "abc123", "main")
    assert view.repository == "acme/shop"


@pytest.mark.contract
def test_local_run_has_its_git_branch_in_its_view(
    sqlite_repo: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    from qasight.services import ci_provenance

    for name in (
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "BUILDKITE",
        "CIRCLECI",
        "JENKINS_URL",
        "TF_BUILD",
        "CI",
    ):
        monkeypatch.delenv(name, raising=False)
    assert ci_provenance.detect_ci_provenance(dict(os.environ)) is None
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    view = view_from_record(_persisted_record(tmp_path / "artifacts"))
    assert view is not None
    assert view.branch == "trunk"
    assert view.base_branch is None
