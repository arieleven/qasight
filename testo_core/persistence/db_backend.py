"""Database persistence backend — upserts a RunRecord via the repository layer."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from testo_core import paths
from testo_core.engine.result import PlanResult
from testo_core.paths import plan_artifacts_dir
from testo_core.persistence.failure_context import failure_metadata
from testo_core.persistence.health import compute_stage_health, run_test_totals
from testo_core.repository.models import RunStatus
from testo_core.security.redaction import redact_text

logger = logging.getLogger(__name__)


class DbBackend:
    """Persist a :class:`PlanResult` as a :class:`RunRecord` in the database."""

    def __init__(self, artifacts_root: Path) -> None:
        self._artifacts_root = artifacts_root
        # Per run: files left out of the history copy, or why the copy failed.
        self._snapshot_notes: dict[str, dict[str, Any]] = {}

    def _local_snapshot_dir(self, plan_name: str) -> str | None:
        """Path of this plan's artifacts relative to ``paths.ORCHESTRATOR_ROOT`` (the data root).

        ``history.snapshots`` reads local ``snapshot_dir`` values relative to
        ``ORCHESTRATOR_ROOT``, so only return a value when the artifacts live under it.
        """
        plan_dir = plan_artifacts_dir(self._artifacts_root, plan_name)
        try:
            return plan_dir.relative_to(paths.ORCHESTRATOR_ROOT).as_posix()
        except ValueError:
            return None

    def _snapshot_run_artifacts(self, run_id: str, plan_name: str) -> str | None:
        """Copy this plan's artifacts to ``static/history/<run_id>/artifacts/``.

        Every run of a cycle writes into the same ``<artifacts>/<cycle>/`` tree,
        so a record pointing there would describe whichever run came last. A
        per-run copy keeps each run's per-test results (Compare's test-level
        diff, the artifact download) after the next run overwrites the tree.
        Returns the copy's path relative to ``ORCHESTRATOR_ROOT``, or None.
        """
        plan_dir = plan_artifacts_dir(self._artifacts_root, plan_name)
        if not plan_dir.is_dir():
            return None
        from testo_core.config.history_section import max_history_file_bytes

        limit = max_history_file_bytes()
        skipped: list[str] = []

        def _too_big(folder: str, names: list[str]) -> set[str]:
            # The cycle lock (and the run marker in it) is not a result.
            out: set[str] = {n for n in names if n == ".lock" and Path(folder) == plan_dir}
            if limit is None:
                return out
            for name in names:
                path = Path(folder) / name
                try:
                    if path.is_file() and path.stat().st_size > limit:
                        out.add(name)
                        skipped.append(path.relative_to(plan_dir).as_posix())
                except OSError:
                    continue
            return out

        dest = paths.STATIC_HISTORY_ROOT / run_id / "artifacts"
        try:
            shutil.copytree(plan_dir, dest, dirs_exist_ok=True, ignore=_too_big)
            self._snapshot_notes[run_id] = {"snapshot_skipped_files": skipped} if skipped else {}
            return dest.relative_to(paths.ORCHESTRATOR_ROOT).as_posix()
        except (OSError, ValueError) as exc:
            # A full disk lands here: say so, drop the half copy, and note it on the run
            # so Compare and flaky detection missing it isn't a mystery.
            logger.warning("could not copy run %s's results into the history: %s", run_id, exc)
            shutil.rmtree(paths.STATIC_HISTORY_ROOT / run_id, ignore_errors=True)
            self._snapshot_notes[run_id] = {"snapshot_error": str(exc)[:300]}
            return None

    def _record_test_results(self, run_id: str, plan_name: str) -> None:
        """Store one row per test (``test_result``) from the run's Allure results."""
        from testo_core.reporting.allure_cases import load_cases
        from testo_core.repository.db import get_test_result_store

        try:
            cases = load_cases(plan_artifacts_dir(self._artifacts_root, plan_name))
            if cases:
                get_test_result_store().replace(run_id, cases)
        except Exception:
            # Broad on purpose: the run row exists; the analytics fall back to the snapshot.
            logger.warning("could not store test rows for run %s", run_id, exc_info=True)

    def _record_run_snapshot(self, repo: Any, run_id: str, plan_name: str) -> None:
        """Point the run record at its own copy; it keeps the shared path otherwise."""
        snapshot_dir = self._snapshot_run_artifacts(run_id, plan_name)
        notes = self._snapshot_notes.pop(run_id, {})
        if not snapshot_dir and not notes:
            return
        patch = {**notes, **({"snapshot_dir": snapshot_dir} if snapshot_dir else {})}
        try:
            repo.merge_run_metadata(run_id, patch)
        except Exception:
            # Broad on purpose: the run row already exists; losing this pointer must not lose its id.
            logger.warning("could not record snapshot for run %s", run_id, exc_info=True)

    def persist(self, result: PlanResult) -> str | None:
        try:
            from testo_core.repository.db import get_repository
            from testo_core.services.ci_provenance import (
                ci_repository,
                detect_ci_provenance,
                detect_git_checkout,
                github_repository_of,
            )

            repo = get_repository()
            status = RunStatus.COMPLETED if result.exit_code == 0 else RunStatus.FAILED

            stage_health, health_pct = compute_stage_health(result, self._artifacts_root)
            if health_pct is None and result.stages:
                passed_stages = sum(1 for s in result.stages if s.returncode == 0)
                health_pct = 100.0 * passed_stages / len(result.stages)
            stage_health_by_name = {h["name"]: h for h in stage_health}
            provenance = detect_ci_provenance(os.environ)
            checkout = {} if provenance else detect_git_checkout(result.target_repo)
            repository = (
                ci_repository(os.environ)
                if provenance
                else github_repository_of(result.target_repo)
            )
            extra_metadata = result.extra.get("run_metadata")
            from testo_core.services import run_environment, run_release

            explicit_env = (
                extra_metadata.get(run_environment.METADATA_KEY)
                if isinstance(extra_metadata, dict)
                else None
            )
            environment = run_environment.resolve(
                explicit_env,
                os.environ,
                plan_artifacts_dir(self._artifacts_root, result.plan_name),
            )
            release = run_release.resolve(
                extra_metadata.get(run_release.METADATA_KEY)
                if isinstance(extra_metadata, dict)
                else None,
                os.environ,
            )

            record = repo.create_run(
                status=status,
                start_time=datetime.fromtimestamp(result.started_at, tz=UTC),
                end_time=datetime.fromtimestamp(result.finished_at, tz=UTC),
                metadata={
                    "plan": result.plan_name,
                    "target_repo": str(result.target_repo)
                    if result.target_repo is not None
                    else None,
                    "test_kind": "cycle",
                    "returncode": int(result.exit_code),
                    "exit_code": int(result.exit_code),
                    "aggregate_returncode": result.aggregate_returncode,
                    "duration_s": result.duration_s,
                    "started_at": result.started_at,
                    "finished_at": result.finished_at,
                    "started_at_iso": datetime.fromtimestamp(result.started_at, tz=UTC).isoformat(),
                    "stage_count": len(result.stages),
                    "stages": [
                        {
                            "name": s.stage_name,
                            "framework": s.framework,
                            "tier": s.tier,
                            "returncode": s.returncode,
                            "duration_s": s.duration_s,
                            "quarantined": list(s.quarantined),
                            "waived_returncode": s.waived_returncode,
                            "crash_kind": s.crash_kind,
                            "crash_detail": redact_text(s.crash_detail) if s.crash_detail else None,
                            **{
                                k: v
                                for k, v in stage_health_by_name.get(s.stage_name, {}).items()
                                if k != "name"
                            },
                        }
                        for s in result.stages
                    ],
                    "health_pct": health_pct,
                    **run_test_totals(stage_health),
                    "snapshot_dir": self._local_snapshot_dir(result.plan_name),
                    "source": "engine",
                    **failure_metadata(result),
                    **(provenance.to_metadata() if provenance else {}),
                    **checkout,
                    **({"repository": repository} if repository else {}),
                    **(extra_metadata if isinstance(extra_metadata, dict) else {}),
                    run_environment.METADATA_KEY: environment or None,
                    run_release.METADATA_KEY: release or None,
                },
            )
            run_id = str(record.id)
            self._record_run_snapshot(repo, run_id, result.plan_name)
            self._record_test_results(run_id, result.plan_name)
            return run_id
        except Exception:
            # Broad on purpose: DB history is best-effort; the JSON backend still has the run.
            logger.warning("db persistence failed for plan %s", result.plan_name, exc_info=True)
            return None
