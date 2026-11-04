"""Copy Allure ``history/`` from a prior archived cycle into current result dirs (trend graphs)."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from rich.console import Console

from qasight.reporting.collector import collect_results
from qasight.services.report_archive import extract_archive_to_plan_dir

logger = logging.getLogger(__name__)


def _copy_matching_history(prior_plan_root: Path, current_results_dir: Path) -> bool:
    fw = current_results_dir.name
    candidates = sorted(prior_plan_root.glob(f"**/allure-results/{fw}/history"))
    if not candidates:
        return False
    src = candidates[-1]
    if not src.is_dir():
        return False
    dest = current_results_dir / "history"
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return True


def try_inject_prior_history(
    *,
    artifacts_root: Path,
    plan_name: str | None,
    console: Console | None,
    enabled: bool,
    trend_depth: int = 1,
) -> None:
    """Best-effort: unpack prior archive(s) for ``plan_name`` and merge ``history`` folders."""
    if not enabled or not plan_name:
        return
    depth = max(1, int(trend_depth))
    try:
        from qasight.repository.db import get_report_archive_repository

        repo = get_report_archive_repository()
        rows = repo.list_recent_for_cycle(cycle_name=plan_name, limit=depth + 1)
        if len(rows) < 2:
            return
        prior_rows = list(reversed(rows[1 : 1 + depth]))

        results = collect_results(artifacts_root, plan_name=plan_name)
        copied_any = False
        for src_row in prior_rows:
            with tempfile.TemporaryDirectory(prefix="testo-history-") as td:
                tmp = Path(td)
                extract_archive_to_plan_dir(
                    zip_bytes=src_row.artifact_bytes,
                    dest_artifacts_root=tmp,
                    plan_name=plan_name,
                )
                prior_root = tmp / plan_name
                if not prior_root.is_dir():
                    continue
                copied_any = (
                    any(_copy_matching_history(prior_root, st.results_dir) for st in results.stages)
                    or copied_any
                )

        if console and copied_any:
            console.print(
                f"[dim]Injected Allure history from up to {depth} prior archived run(s) (trends).[/]"
            )
    except Exception:
        # Broad on purpose: trend history is optional; DB, zip and copy errors must not fail a run.
        logger.warning("Allure history injection skipped", exc_info=True)
