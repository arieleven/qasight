"""Thin wrapper around Allure Report 3 ``generate`` and ``open``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from qasight.reporting.allure_cli import (
    AllureCLINotFoundError,
    is_allure_available,
    report_has_index,
    run_generate,
    run_open_blocking,
)

__all__ = [
    "AllureCLINotFoundError",
    "AllureGenerateResult",
    "generate_html",
    "is_allure_available",
    "serve_results",
]


@dataclass(frozen=True)
class AllureGenerateResult:
    ok: bool
    out_dir: Path
    message: str


def generate_html(
    *,
    result_dirs: Sequence[Path],
    out_dir: Path,
    clean: bool = True,
    single_file: bool = False,
) -> AllureGenerateResult:
    """Invoke Allure 3 ``generate`` (or ``awesome --single-file``) and capture its result."""
    if not result_dirs:
        return AllureGenerateResult(
            ok=False,
            out_dir=out_dir,
            message="no allure-results directories found.",
        )
    if not is_allure_available():
        raise AllureCLINotFoundError(
            "the Allure Report 3 CLI was not found. Install Node.js 18+, run "
            "`npm install` in the repo root, or use --format json/junit instead."
        )

    out_dir = out_dir.expanduser().resolve()
    try:
        completed = run_generate(
            result_dirs=result_dirs,
            out_dir=out_dir,
            clean=clean,
            single_file=single_file,
        )
    except AllureCLINotFoundError:
        raise
    except FileNotFoundError as exc:
        raise AllureCLINotFoundError(str(exc)) from exc

    ok = completed.returncode == 0 and report_has_index(out_dir)
    msg = (
        (completed.stdout or "").strip()
        or (completed.stderr or "").strip()
        or ("report generated" if ok else f"allure exited {completed.returncode}")
    )
    return AllureGenerateResult(ok=ok, out_dir=out_dir, message=msg)


def serve_results(*, result_dirs: Sequence[Path], port: int = 8080) -> int:
    """Generate and serve raw result directories via ``allure open`` (blocks until stopped)."""
    if not result_dirs:
        return 1
    if not is_allure_available():
        raise AllureCLINotFoundError(
            "the Allure Report 3 CLI was not found. Install Node.js 18+, run "
            "`npm install` in the repo root, or use --format json/junit instead."
        )
    return run_open_blocking(paths=result_dirs, port=port)
