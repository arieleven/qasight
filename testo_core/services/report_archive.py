"""Zip / unzip cycle artifacts for :class:`~testo_core.repository.models.ReportArchive`."""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from pathlib import Path
from typing import Any

from testo_core.paths import plan_artifacts_dir


def _empty_metrics() -> dict[str, int | None]:
    return {
        "total_tests": None,
        "passed": None,
        "failed": None,
        "broken": None,
        "skipped": None,
        "unknown": None,
        "allure_duration_ms": None,
        "plan_duration_ms": None,
    }


def aggregate_cycle_metrics(plan_dir: Path) -> dict[str, int | None]:
    """Sum Allure KPIs across every ``.../allure-results/<fw>/`` tree under ``plan_dir``."""
    plan_dir = plan_dir.expanduser().resolve()
    if not plan_dir.is_dir():
        return _empty_metrics()

    from testo_core.reporting.collector import collect_results
    from testo_core.reporting.metrics import parse_allure_results_dir

    cr = collect_results(plan_dir.parent, plan_name=plan_dir.name)
    total_tests = passed = failed = broken = skipped = unknown = 0
    allure_duration_ms = 0
    for rd in cr.result_dirs:
        m = parse_allure_results_dir(rd)
        total_tests += m.total_tests
        passed += m.passed
        failed += m.failed
        broken += m.broken
        skipped += m.skipped
        unknown += m.unknown
        allure_duration_ms += m.duration_ms

    plan_duration_ms: int | None = None
    pr = plan_dir / "plan_result.json"
    if pr.is_file():
        try:
            data = json.loads(pr.read_text(encoding="utf-8"))
            ds = data.get("duration_s")
            if ds is not None:
                plan_duration_ms = int(float(ds) * 1000.0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    return {
        "total_tests": total_tests,
        "passed": passed,
        "failed": failed,
        "broken": broken,
        "skipped": skipped,
        "unknown": unknown,
        "allure_duration_ms": allure_duration_ms,
        "plan_duration_ms": plan_duration_ms,
    }


def build_cycle_zip_bytes(
    artifacts_root: Path,
    plan_name: str,
    *,
    exit_code_override: int | None = None,
) -> tuple[bytes, dict[str, Any], int]:
    """Zip one cycle directory under ``artifacts_root``; return payload, summary, exit code."""
    plan_dir = plan_artifacts_dir(artifacts_root, plan_name)
    if not plan_dir.is_dir():
        raise FileNotFoundError(f"plan artifacts not found: {plan_dir}")

    from testo_core.config.history_section import max_history_file_bytes

    # history.max_file_mb: videos and traces bigger than this stay out of the archive.
    limit = max_history_file_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(plan_dir.rglob("*")):
            if path.is_file():
                if path.parent == plan_dir and path.name == ".lock":
                    continue  # the cycle lock, not a result
                if limit is not None and path.stat().st_size > limit:
                    continue
                arc = path.relative_to(plan_dir).as_posix()
                zf.write(path, arcname=arc)

    summary: dict[str, Any] = {}
    exit_code = 0
    pr = plan_dir / "plan_result.json"
    if pr.is_file():
        try:
            summary = json.loads(pr.read_text(encoding="utf-8"))
            exit_code = int(summary.get("exit_code", 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            summary = {}
            exit_code = 0
    if exit_code_override is not None:
        exit_code = int(exit_code_override)

    return buf.getvalue(), summary, exit_code


def extract_archive_to_plan_dir(
    *, zip_bytes: bytes, dest_artifacts_root: Path, plan_name: str
) -> Path:
    """Extract a stored zip so paths match ``plan_artifacts_dir`` layout."""
    dest = plan_artifacts_dir(dest_artifacts_root, plan_name)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        _safe_extract(zf, dest)
    return dest


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract zip members only when they stay inside ``dest``."""
    dest = dest.expanduser().resolve()
    for info in zf.infolist():
        member_path = (dest / info.filename).resolve()
        try:
            member_path.relative_to(dest)
        except ValueError as exc:
            raise ValueError(f"archive member escapes destination: {info.filename!r}") from exc
        zf.extract(info, dest)


def try_persist_cycle_report(
    *,
    artifacts_root: Path,
    plan_name: str,
    exit_code_override: int | None = None,
) -> uuid.UUID | None:
    """Best-effort insert of a zipped cycle directory into ``ReportArchive``."""
    from testo_core.repository.db import get_report_archive_repository

    log = logging.getLogger(__name__)
    try:
        payload, summary, ec = build_cycle_zip_bytes(
            artifacts_root.expanduser().resolve(),
            plan_name,
            exit_code_override=exit_code_override,
        )
        plan_dir = plan_artifacts_dir(artifacts_root.expanduser().resolve(), plan_name)
        metrics = aggregate_cycle_metrics(plan_dir)
        row = get_report_archive_repository().insert(
            cycle_name=plan_name,
            exit_code=ec,
            summary_json=summary,
            artifact_bytes=payload,
            **metrics,
        )
        return row.id
    except Exception:
        # Broad on purpose: the caller turns None into exit 3 under --ci (or ignores it otherwise).
        log.exception("report archive persistence failed for cycle %s", plan_name)
        return None
