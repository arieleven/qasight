"""Typed views of a stored run, decoupled from the ``RunRecord.metadata_`` JSON shape.

Records are written by :class:`~qasight.persistence.db_backend.DbBackend`
(``exit_code``, ``duration_s``, ``stages``, ...). :func:`view_from_record` maps that
JSON onto typed fields, so nothing above this module depends on the raw keys. The
promoted columns (``cycle``, ``health_pct``, ``branch``, ...) win when set; the JSON
is the fallback for rows migration 0002 could not fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qasight.repository.models import RunRecord, RunStatus


@dataclass(frozen=True)
class CompletedRunView:
    """Flat view of one run for the API and services."""

    run_id: str
    status: RunStatus | None
    created_at: float
    started_at: float
    finished_at: float
    test_kind: str
    returncode: int
    wall_duration_ms: float
    metrics_duration_ms: int | None
    total_tests: int | None
    passed: int | None
    failed: int | None
    broken: int | None
    skipped: int | None
    avg_case_ms: float | None
    health_pct: float | None
    target_repo: str | None
    snapshot_dir: str | None
    audit_json: str | None
    cycle: str | None = None
    stage_health: list[dict[str, Any]] = field(default_factory=list)
    # Branch and commit the run tested (CI provenance, else the local checkout).
    branch: str | None = None
    commit: str | None = None
    # A pull/merge request's target branch: the baseline its changes are judged against.
    base_branch: str | None = None
    # What it ran on, "browser=firefox, os=linux" (services/run_environment.py); None if not set.
    environment: str | None = None
    # The release it tested, "2.3" (services/run_release.py); None if not set.
    release: str | None = None
    # ``owner/name`` on the code host (CI variables, an upload, or a github.com ``origin``).
    repository: str | None = None
    # Line coverage from ``--coverage`` (covered lines out of all lines), when given.
    coverage_pct: float | None = None
    coverage_covered: int | None = None
    coverage_total: int | None = None


@dataclass(frozen=True)
class RunSessionView:
    """A run plus the report links available for it (dashboard and run list)."""

    run_id: str
    created_at: float
    returncode: int
    health_pct: float | None
    total_tests: int | None
    passed: int | None
    failed: int | None
    skipped: int | None
    broken: int | None
    status: RunStatus | None
    links_under_static: dict[str, str]
    cycle: str | None = None
    branch: str | None = None
    commit: str | None = None
    environment: str | None = None
    release: str | None = None


def wall_duration_ms_from_metadata(md: dict[str, Any]) -> float:
    """``duration_s`` in milliseconds, else ``finished_at - started_at``."""
    if md.get("duration_s") is not None:
        return max(0.0, float(md["duration_s"]) * 1000.0)
    started, finished = md.get("started_at"), md.get("finished_at")
    if started is not None and finished is not None:
        return max(0.0, (float(finished) - float(started)) * 1000.0)
    return 0.0


def health_pct_from_metadata(md: dict[str, Any]) -> float | None:
    """Stored health %, else the share of stages that exited ``0``."""
    if md.get("health_pct") is not None:
        return float(md["health_pct"])
    stages = md.get("stages")
    if isinstance(stages, list) and stages:
        passed_stages = sum(1 for s in stages if isinstance(s, dict) and s.get("returncode") == 0)
        return 100.0 * passed_stages / len(stages)
    return None


def _optional_int(md: dict[str, Any], key: str) -> int | None:
    return int(md[key]) if md.get(key) is not None else None


def _optional_float(md: dict[str, Any], key: str) -> float | None:
    return float(md[key]) if md.get(key) is not None else None


def _optional_str(md: dict[str, Any], key: str) -> str | None:
    return str(md[key]) if md.get(key) else None


def _number(md: dict[str, Any], key: str) -> float | None:
    """A numeric value that may be stored as a string (uploaded metadata); ``None`` if not."""
    try:
        return float(md[key]) if md.get(key) not in (None, "") else None
    except (TypeError, ValueError):
        return None


def view_from_record(record: RunRecord) -> CompletedRunView | None:
    """Build a :class:`CompletedRunView`; ``None`` for records without a ``run_id``."""
    md = record.metadata_ or {}
    run_id = md.get("run_id")
    if not run_id:
        return None
    return CompletedRunView(
        run_id=str(run_id),
        status=record.status,
        created_at=float(md.get("created_at") or 0.0),
        started_at=float(md.get("started_at") or 0.0),
        finished_at=float(md.get("finished_at") or 0.0),
        test_kind=str(md.get("test_kind") or "unknown"),
        cycle=record.cycle or _optional_str(md, "plan"),
        returncode=int(md.get("exit_code") or 0),
        wall_duration_ms=wall_duration_ms_from_metadata(md),
        metrics_duration_ms=_optional_int(md, "metrics_duration_ms"),
        total_tests=_optional_int(md, "total_tests"),
        passed=_optional_int(md, "passed"),
        failed=_optional_int(md, "failed"),
        broken=_optional_int(md, "broken"),
        skipped=_optional_int(md, "skipped"),
        avg_case_ms=_optional_float(md, "avg_case_ms"),
        health_pct=record.health_pct
        if record.health_pct is not None
        else health_pct_from_metadata(md),
        target_repo=_optional_str(md, "target_repo"),
        snapshot_dir=record.snapshot_dir or _optional_str(md, "snapshot_dir"),
        audit_json=_optional_str(md, "audit_json"),
        stage_health=[s for s in md.get("stages") or [] if isinstance(s, dict)],
        branch=record.branch or _optional_str(md, "ci_ref_name") or _optional_str(md, "git_branch"),
        commit=record.commit_sha
        or _optional_str(md, "ci_commit_sha")
        or _optional_str(md, "git_commit"),
        base_branch=_optional_str(md, "ci_base_ref"),
        environment=_optional_str(md, "environment"),
        release=_optional_str(md, "release"),
        repository=_optional_str(md, "repository"),
        coverage_pct=_number(md, "coverage_pct"),
        coverage_covered=int(n) if (n := _number(md, "coverage_covered")) is not None else None,
        coverage_total=int(n) if (n := _number(md, "coverage_total")) is not None else None,
    )
