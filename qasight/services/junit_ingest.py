"""Record uploaded test result files (JUnit XML and the other formats junit_import reads) as a run: the server side of ``qasight ingest --server``.

CI jobs anywhere upload their JUnit XML to one QASight server
(``POST /api/v1/ingest``), so the whole team's runs land in one history. The
upload is written to a scratch directory and recorded through
:class:`~qasight.services.cycle_run.CycleRunService` with a one-stage
``junit`` cycle, exactly like a local ``qasight ingest``: same engine, same run
record, same reporters. Nothing in an upload is ever executed.

The client's CI provenance (provider, commit, branch, ...) travels in the
request, because the server's own environment says nothing about it.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path

from qasight.config.errors import ConfigValidationError
from qasight.config.schema import QASightConfig

logger = logging.getLogger(__name__)

# Upload limits: plenty for real suites, small enough that one request can't fill a disk.
MAX_FILES = 200
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024

# Run-record keys an upload may set. Everything else is computed by the server.
RUN_METADATA_KEYS: tuple[str, ...] = (
    "ci_provider",
    "ci_pipeline_id",
    "ci_job_id",
    "ci_commit_sha",
    "ci_ref_name",
    "ci_base_ref",
    "repository",
    # "browser=firefox, os=linux" (services/run_environment.py).
    "environment",
    # "2.3" (services/run_release.py).
    "release",
    # Line coverage the client read from its coverage report (``--coverage``).
    "coverage_pct",
    "coverage_covered",
    "coverage_total",
)
_MAX_METADATA_CHARS = 256
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")

# One ingest at a time: they take milliseconds, and the run history (often
# SQLite) and report archive are shared by every request.
_INGEST_LOCK = threading.Lock()


@dataclass(frozen=True)
class IngestOutcome:
    run_id: str | None
    cycle: str
    exit_code: int
    tests: int
    passed: int
    failed: int
    skipped: int


def clean_run_metadata(raw: Mapping[str, str | None]) -> dict[str, str]:
    """Keep the allowed, non-empty provenance keys, trimmed to a sane length."""
    from qasight.reporting.coverage_import import METADATA_KEYS, clean_coverage_metadata

    out: dict[str, str] = {}
    for key in RUN_METADATA_KEYS:
        value = (raw.get(key) or "").strip()
        if value and key not in METADATA_KEYS:
            out[key] = value[:_MAX_METADATA_CHARS]
    # Numbers, not free text: kept only when covered <= total.
    out.update(clean_coverage_metadata(raw))
    return out


def ingest_uploaded_reports(
    *,
    cfg: QASightConfig,
    cycle: str,
    reports: Sequence[tuple[str, bytes]],
    stage: str = "junit",
    tier: str = "unit",
    run_metadata: Mapping[str, str] | None = None,
) -> IngestOutcome:
    """Record *reports* (``(filename, content)`` pairs) as one run of *cycle*.

    Raises :class:`ConfigValidationError` for an empty or oversized upload, or
    an unsafe cycle or stage name.
    """
    if not reports:
        raise ConfigValidationError("no test result files in the upload.")
    if len(reports) > MAX_FILES:
        raise ConfigValidationError(f"too many files ({len(reports)}; the limit is {MAX_FILES}).")
    total = 0
    for name, content in reports:
        if len(content) > MAX_FILE_BYTES:
            raise ConfigValidationError(
                f"{name!r} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB."
            )
        total += len(content)
    if total > MAX_TOTAL_BYTES:
        raise ConfigValidationError(
            f"upload is larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MB in total."
        )

    def write(reports_dir: Path) -> None:
        used: set[str] = set()
        for index, (name, content) in enumerate(reports):
            # Client file names are only a label; what a file is comes from its content
            # (junit_import). The name is kept when it is safe, because an Allure result
            # names its attachments by file name; otherwise the index keeps it unique.
            filename = _stored_name(name, index, used)
            used.add(filename)
            (reports_dir / filename).write_bytes(content)

    return ingest_report_files(
        cfg=cfg, cycle=cycle, write=write, stage=stage, tier=tier, run_metadata=run_metadata
    )


def ingest_report_files(
    *,
    cfg: QASightConfig,
    cycle: str,
    write: Callable[[Path], None],
    stage: str = "junit",
    tier: str = "unit",
    run_metadata: Mapping[str, str] | None = None,
) -> IngestOutcome:
    """Record the result files ``write`` puts in the folder it is given as one run of *cycle*.

    One ``junit`` stage, the same engine, run record and reporters as ``qasight ingest``.
    On the team server (:func:`separate_process`) the recording runs in a worker process,
    so a big upload doesn't hold up the API's other requests. Raises
    :class:`ConfigValidationError` for an unsafe cycle or stage name.
    """
    with _INGEST_LOCK, tempfile.TemporaryDirectory(prefix="testo-ingest-") as scratch:
        root = Path(scratch)
        reports_dir = root / "reports"
        reports_dir.mkdir()
        write(reports_dir)
        metadata = dict(run_metadata or {})
        if not separate_process():
            return _record(root, cfg, cycle, stage, tier, metadata)
        return _in_worker(root, cfg, cycle, stage, tier, metadata)


def separate_process() -> bool:
    """Whether uploads are recorded in a worker process (``TESTO_INGEST_PROCESS``).

    ``qasight-api`` and ``qasight ui`` turn it on: recording is CPU-bound Python, and in
    the API's own process it took turns with every page request (8 uploads of 5,000
    tests: 16 s alone, minutes next to a busy UI; docs/Specs & ADRs/Stress Test -
    2026-11-20.md). ``0`` records in the request's thread, as the CLI and tests do.
    """
    return os.environ.get("TESTO_INGEST_PROCESS", "0").strip().lower() in ("1", "true", "yes")


_pool_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
# A fresh worker after this many uploads gives back the memory a big one took.
_TASKS_PER_WORKER = 50


def _worker_pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            # spawn: the API process has threads, which fork would copy mid-flight.
            _pool = ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
                max_tasks_per_child=_TASKS_PER_WORKER,
            )
        return _pool


def _in_worker(
    root: Path, cfg: QASightConfig, cycle: str, stage: str, tier: str, metadata: dict[str, str]
) -> IngestOutcome:
    global _pool
    try:
        return _worker_pool().submit(_record, root, cfg, cycle, stage, tier, metadata).result()
    except BrokenProcessPool:
        # The worker died (killed, out of memory): start a new one for the next upload.
        logger.error("the upload worker stopped; recording this upload in the API process")
        with _pool_lock:
            _pool = None
        return _record(root, cfg, cycle, stage, tier, metadata)


def shutdown_worker() -> None:
    """Stop the upload worker (server shutdown, tests)."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=True)


def _record(
    root: Path, cfg: QASightConfig, cycle: str, stage: str, tier: str, metadata: dict[str, str]
) -> IngestOutcome:
    """Record ``root/reports`` as one run (in the API process or the upload worker)."""
    from qasight.services.cycle_run import (
        CycleRunOptions,
        CycleRunService,
        junit_ingest_plan,
    )

    plan = junit_ingest_plan(
        cycle=cycle,
        reports=["reports/*"],
        target_repo=root,
        stage_name=stage,
        tier=tier,
    )
    # The scratch path means nothing after this call; record the client's repository instead.
    run_metadata: dict[str, str | None] = {**metadata}
    run_metadata["target_repo"] = run_metadata.get("repository")
    outcome = CycleRunService().run(
        cfg=cfg,
        plan=plan,
        renderer=_SilentRenderer(),
        options=CycleRunOptions(
            artifacts_root=root / "artifacts",
            run_metadata=run_metadata,
            force=True,
        ),
    )
    # The DB backend copied the artifacts to static/history/<run_id>/; the
    # scratch directory goes when the caller's block ends.
    counts = _status_counts(root / "artifacts" / plan.name / stage / "allure-results" / "junit")
    return IngestOutcome(
        run_id=outcome.run_id,
        cycle=plan.name,
        exit_code=outcome.exit_code,
        tests=sum(counts.values()),
        passed=counts.get("passed", 0),
        failed=counts.get("failed", 0) + counts.get("broken", 0),
        skipped=counts.get("skipped", 0),
    )


def _stored_name(name: str, index: int, used: set[str]) -> str:
    raw = Path(name or "report").name
    stem = _UNSAFE_FILENAME.sub("_", Path(raw).stem)[:80].strip("._") or "report"
    suffix = Path(raw).suffix.lower()
    if not _SAFE_SUFFIX.fullmatch(suffix):
        suffix = ".xml"
    candidate = f"{stem}{suffix}"
    if candidate in used:
        candidate = f"{index:03d}-{candidate}"
    return candidate


def _status_counts(results_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in results_dir.glob("*-result.json"):
        try:
            status = str(json.loads(path.read_text(encoding="utf-8")).get("status") or "unknown")
        except (OSError, ValueError):
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


class _SilentRenderer:
    wants_streaming = False

    def handle(self, event: object) -> None:
        return
