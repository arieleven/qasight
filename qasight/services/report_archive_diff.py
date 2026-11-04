"""Compare two archived Allure cycles (per-test and aggregate metrics)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from qasight.paths import plan_artifacts_dir
from qasight.reporting.allure_cases import case_key, case_stage, label_value, load_cases
from qasight.repository.models import ReportArchive

# The parser lives in reporting.allure_cases; these names stay for the modules that import them.
_case_key = case_key
_case_stage = case_stage
_label_value = label_value
_load_cases = load_cases


CaseChangeKind = Literal["added", "removed", "regression", "fix", "status_change"]


@dataclass(frozen=True)
class CaseChange:
    key: str
    name: str
    group: str
    baseline_status: str | None
    current_status: str | None
    kind: CaseChangeKind
    duration_delta_ms: int | None


def case_identity(case: dict[str, Any]) -> str:
    """A test's name with the separators runners disagree on folded to ``.``.

    pytest's own results name a test ``tests.test_a#test_a`` and its JUnit XML
    ``tests.test_a.test_a``; Jest, Go and others use ``/``, `` > `` or ``::``.
    """
    raw = str(case.get("full_name") or case.get("name") or "").strip()
    return re.sub(r"\s*(?:#|::|/|\s>\s)\s*", ".", raw)


def align_case_keys(
    base_cases: dict[str, dict[str, Any]], cur_cases: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """*base_cases* re-keyed so a test recorded two different ways matches itself.

    The key is the source's own id (Allure's ``historyId`` for ``qasight run``, a
    hash of tool and name for ``qasight ingest``), so the same suite recorded by
    ``qasight run``, ``pytest --qasight`` and ``qasight ingest`` gets different keys
    and would read as every test removed and added again. A key found on only
    one side is paired with one on the other side that has the same
    :func:`case_identity`, when exactly one test on each side has it.
    """
    only_base = [k for k in base_cases if k not in cur_cases]
    only_cur = [k for k in cur_cases if k not in base_cases]
    if not only_base or not only_cur:
        return base_cases

    def by_identity(cases: dict[str, dict[str, Any]], keys: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for key in keys:
            ident = case_identity(cases[key])
            if ident:
                out.setdefault(ident, []).append(key)
        return out

    base_ids = by_identity(base_cases, only_base)
    cur_ids = by_identity(cur_cases, only_cur)
    renames = {
        base_keys[0]: cur_ids[ident][0]
        for ident, base_keys in base_ids.items()
        if len(base_keys) == 1 and len(cur_ids.get(ident, ())) == 1
    }
    if not renames:
        return base_cases
    return {renames.get(k, k): v for k, v in base_cases.items()}


def diff_case_maps(
    base_cases: dict[str, dict[str, Any]], cur_cases: dict[str, dict[str, Any]]
) -> list[CaseChange]:
    """Match two ``_load_cases()`` maps by key and classify each case's change.

    Shared by both ``diff_archives`` (ReportArchive zips, ``qasight diff``) and
    ``run_snapshot_diff.diff_run_snapshots`` (live run snapshot dirs, the API).
    Keys that differ only because the runs were recorded different ways are
    matched first (:func:`align_case_keys`).
    """
    base_cases = align_case_keys(base_cases, cur_cases)
    changes: list[CaseChange] = []
    all_keys = sorted(set(base_cases) | set(cur_cases))
    for key in all_keys:
        b = base_cases.get(key)
        c = cur_cases.get(key)
        bs = b["status"] if b else None
        cs = c["status"] if c else None
        name = (c or b or {}).get("name", key) if (c or b) else key
        group = str((c or b or {}).get("group") or "(ungrouped)")
        bd = (b or {}).get("duration_ms") if b else None
        cd = (c or {}).get("duration_ms") if c else None
        delta = None
        if isinstance(bd, int) and isinstance(cd, int):
            delta = cd - bd

        kind: CaseChangeKind | None
        if b is None and c is not None:
            kind = "added"
        elif b is not None and c is None:
            kind = "removed"
        else:
            assert b is not None and c is not None
            if bs == cs:
                kind = None
            elif bs in {"passed"} and cs in {"failed", "broken"}:
                kind = "regression"
            elif bs in {"failed", "broken"} and cs in {"passed"}:
                kind = "fix"
            else:
                kind = "status_change"

        if kind is not None:
            changes.append(
                CaseChange(
                    key=key[:80],
                    name=str(name)[:120],
                    group=group[:100],
                    baseline_status=bs,
                    current_status=cs,
                    kind=kind,
                    duration_delta_ms=delta,
                )
            )
    return changes


def diff_archives(
    *, baseline: ReportArchive, current: ReportArchive, tmp: Path
) -> tuple[list[CaseChange], dict[str, Any]]:
    """Extract both zips under ``tmp`` and return case-level changes plus metrics row dict."""
    from qasight.services.report_archive import extract_archive_to_plan_dir

    extract_archive_to_plan_dir(
        zip_bytes=baseline.artifact_bytes,
        dest_artifacts_root=tmp / "a",
        plan_name=baseline.cycle_name,
    )
    extract_archive_to_plan_dir(
        zip_bytes=current.artifact_bytes,
        dest_artifacts_root=tmp / "b",
        plan_name=current.cycle_name,
    )
    base_root = plan_artifacts_dir(tmp / "a", baseline.cycle_name)
    cur_root = plan_artifacts_dir(tmp / "b", current.cycle_name)
    base_cases = _load_cases(base_root)
    cur_cases = _load_cases(cur_root)
    changes = diff_case_maps(base_cases, cur_cases)

    metrics = {
        "baseline_id": str(baseline.id),
        "current_id": str(current.id),
        "baseline_total_tests": baseline.total_tests,
        "current_total_tests": current.total_tests,
        "baseline_passed": baseline.passed,
        "current_passed": current.passed,
        "baseline_failed": baseline.failed,
        "current_failed": current.failed,
        "baseline_plan_duration_ms": baseline.plan_duration_ms,
        "current_plan_duration_ms": current.plan_duration_ms,
    }
    return changes, metrics


def parse_archive_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, TypeError):
        return None
