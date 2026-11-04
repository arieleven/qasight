from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qasight.paths import (
    STATIC_ALLURE_HTML,
    STATIC_ALLURE_INDEX,
    STATIC_ALLURE_REPORT_DIR,
    STATIC_ALLURE_REPORTS_DIR,
    STATIC_DIR,
    allure_report_dir,
)

logger = logging.getLogger(__name__)


def _flatten_allure_result_json(*, root: Path) -> int:
    """
    BehaveX sometimes writes Allure results under a nested folder (e.g. ``behave/allure``).
    To ensure the Allure CLI always finds JSON without relying on recursive scanning behavior,
    move any nested ``*-result.json`` files up into ``root``.

    Returns the number of files moved.
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        return 0

    moved = 0
    try:
        for p in root.rglob("*-result.json"):
            if p.parent == root:
                continue
            dest = root / p.name
            if dest.exists():
                # Avoid overwriting; keep the nested copy in place if collision happens.
                continue
            try:
                p.replace(dest)
                moved += 1
            except OSError:
                continue
    except OSError:
        return 0
    return moved


@dataclass(frozen=True)
class ReportPaths:
    results_dir: Path
    report_dir: Path
    zip_path: Path


def _invoke_allure_generate(
    *,
    result_dirs: list[Path],
    report_dir: Path,
    single_file: bool,
    subprocess_run: Callable[..., Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Thin wrapper so tests can inject ``subprocess_run`` without patching ``subprocess``."""
    from qasight.reporting.allure_cli import run_generate

    return run_generate(
        result_dirs=result_dirs,
        out_dir=report_dir,
        clean=True,
        single_file=single_file,
        subprocess_run=subprocess_run,
    )


def compute_system_health_pct(results_dir: Path) -> float | None:
    """
    Overall pass rate from Allure ``*-result.json`` files under ``results_dir``:
    ``100.0 * passed / total`` (tests with status ``passed`` vs all result files).
    """
    from .metrics import parse_allure_results_dir

    results_dir = results_dir.expanduser().resolve()
    if not results_dir.is_dir():
        return None
    m = parse_allure_results_dir(results_dir)
    if m.total_tests <= 0:
        return None
    return (m.passed / m.total_tests) * 100.0


def generate_allure_html(
    *,
    results_dir: Path,
    report_dir: Path,
    input_dirs: list[Path] | None = None,
    subprocess_run: Callable[..., Any] | None = None,
) -> tuple[bool, str, float | None]:
    """
    Generate Allure HTML via Allure Report 3 (Node.js CLI):
      ``allure awesome --single-file`` or ``allure generate`` with ``allurerc.mjs``.

    Each report is generated from an isolated framework-specific results directory.

    Returns ``(ok, message, health_pct)`` where ``health_pct`` is passed/total from result JSON files.

    Requires Node.js 18+ and ``npm install`` in the repo root (or ``TESTO_ALLURE_BIN``).
    """
    results_dir = results_dir.expanduser().resolve()
    report_dir = report_dir.expanduser().resolve()

    if not results_dir.exists():
        return False, f"Results dir does not exist: {results_dir}", None

    # Each report MUST be generated from an isolated input directory.
    use_inputs = (
        [results_dir] if input_dirs is None else [p.expanduser().resolve() for p in input_dirs]
    )
    for d in use_inputs:
        d.mkdir(parents=True, exist_ok=True)

    # BehaveX sanity check + flatten: BehaveX sometimes nests results under a subfolder (e.g. behavex/allure).
    # Move nested ``*-result.json`` files up into the root of the BehaveX results dir.
    for d in use_inputs:
        if d.name == "behavex":
            moved = _flatten_allure_result_json(root=d)
            if moved:
                print(f"[allure] flattened BehaveX results: moved {moved} *-result.json into {d}")

    def _count_results(p: Path) -> int:
        try:
            return len(list(p.rglob("*-result.json")))
        except OSError:
            return 0

    counts = {p.name: _count_results(p) for p in use_inputs}
    print("[allure] input counts: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    if "behavex" in counts and counts["behavex"] == 0:
        print(
            f"[allure] WARNING: no BehaveX Allure result files found under {results_dir / 'behavex'}"
        )

    report_dir.mkdir(parents=True, exist_ok=True)

    from qasight.reporting.allure_cli import AllureCLINotFoundError

    try:
        p = _invoke_allure_generate(
            result_dirs=use_inputs,
            report_dir=report_dir,
            single_file=True,
            subprocess_run=subprocess_run,
        )
    except FileNotFoundError:
        return False, "Allure Report 3 CLI not found. Run `npm install` in the repo root.", None
    except AllureCLINotFoundError as exc:
        return False, str(exc), None

    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        return False, f"Allure generation failed (exit {p.returncode}). {err[:2000]}", None

    publish_allure_index_to_static(report_dir=report_dir)
    health = compute_system_health_pct(results_dir)
    return True, f"Allure report generated at {report_dir}", health


def generate_allure_reports(
    *,
    results_dir: Path,
    frameworks: list[str] | None = None,
    subprocess_run: Callable[..., Any] | None = None,
) -> dict[str, tuple[bool, str, float | None]]:
    """
    Generate per-framework Allure HTML reports (strict isolation).

    Each framework is generated into its own directory:
      ``static/allure_reports/<framework>/index.html``

    Returns: mapping of ``framework_name`` → ``(ok, message, health_pct)``.
    """
    results_dir = results_dir.expanduser().resolve()
    out: dict[str, tuple[bool, str, float | None]] = {}

    if not frameworks:
        frameworks = ["pytest", "behavex", "behave_native"]

    def _pick_allure_inputs(framework_results_dir: Path) -> list[Path]:
        """
        Allure CLI builds may ignore nested JSON when only a parent dir is supplied.

        Our runners may scope results under: <framework>/<run_id>/...
        Prefer passing the run_id subfolders as explicit inputs when present.
        """
        framework_results_dir = framework_results_dir.expanduser().resolve()
        try:
            children = [p for p in framework_results_dir.iterdir() if p.is_dir()]
        except OSError:
            children = []

        if not children:
            return [framework_results_dir]

        picked: list[Path] = []
        for d in children:
            try:
                if any(d.rglob("*-result.json")):
                    picked.append(d)
            except OSError:
                continue
        return picked or [framework_results_dir]

    for fw in frameworks:
        fw_results = results_dir / fw
        fw_results.mkdir(parents=True, exist_ok=True)
        fw_out_dir = allure_report_dir(fw)
        fw_out_dir.mkdir(parents=True, exist_ok=True)

        input_dirs = _pick_allure_inputs(fw_results)
        # BehaveX flattening: results can be nested under the run folder too.
        if str(fw) == "behavex":
            for d in input_dirs:
                _flatten_allure_result_json(root=d)

        ok, msg, hp = generate_allure_html(
            results_dir=fw_results,
            report_dir=fw_out_dir,
            input_dirs=input_dirs,
            subprocess_run=subprocess_run,
        )
        out[str(fw)] = (ok, msg, hp)
    return out


def read_single_file_html(*, report_dir: Path) -> tuple[bool, str, bytes | None]:
    """
    Read the generated single-file Allure report (index.html).
    """
    report_dir = report_dir.expanduser().resolve()
    index = report_dir / "index.html"
    if not index.exists():
        return False, f"Missing {index}. Generate the report first.", None
    try:
        return True, "OK", index.read_bytes()
    except OSError as exc:
        return False, f"Failed reading {index}: {exc}", None


def make_report_zip(*, report_dir: Path, out_dir: Path, base_name: str = "allure-report") -> Path:
    """
    Create a zip archive of the Allure HTML report directory.
    Returns the full path to the generated zip file.
    """
    report_dir = report_dir.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    zip_base = out_dir / base_name
    zip_path = Path(shutil.make_archive(str(zip_base), "zip", root_dir=str(report_dir)))
    return zip_path


def _ensure_static_dirs() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def _chmod_tree(path: Path, *, dirmode: int = 0o755, filemode: int = 0o644) -> None:
    for root, dirs, files in os.walk(path):
        for name in files:
            fp = Path(root) / name
            try:
                os.chmod(fp, filemode)
            except OSError:
                pass
        for name in dirs:
            dp = Path(root) / name
            try:
                os.chmod(dp, dirmode)
            except OSError:
                pass
    try:
        os.chmod(path, dirmode)
    except OSError:
        pass


def _mirror_file_readable(src: Path, dst: Path) -> None:
    """Copy HTML into ``static/`` with world-readable permissions."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    try:
        os.chmod(dst, 0o644)
    except OSError:
        pass


def publish_allure_index_to_static(*, report_dir: Path) -> Path | None:
    """
    When ``report_dir`` is ``static/allure_report``, Allure already wrote ``index.html`` there;
    make the tree readable. Otherwise mirror ``index.html`` to ``static/allure_report.html``,
    which :mod:`qasight.services.report_service` still looks for.
    """
    report_dir = report_dir.expanduser().resolve()
    src = report_dir / "index.html"
    if not src.is_file():
        return None
    _ensure_static_dirs()
    # If report is already under the new static tree, just chmod.
    try:
        if STATIC_ALLURE_REPORTS_DIR.resolve() in report_dir.resolve().parents:
            _chmod_tree(report_dir)
            return src
    except OSError:
        logger.debug("could not resolve %s against the static tree", report_dir, exc_info=True)
    if report_dir.resolve() == STATIC_ALLURE_REPORT_DIR.resolve():
        _chmod_tree(report_dir)
        return STATIC_ALLURE_INDEX if STATIC_ALLURE_INDEX.is_file() else src
    _mirror_file_readable(src, STATIC_ALLURE_HTML)
    return STATIC_ALLURE_HTML


def default_report_paths(*, artifacts_root: Path) -> ReportPaths:
    artifacts_root = artifacts_root.expanduser().resolve()
    results_dir = artifacts_root / "allure-results"
    report_dir = STATIC_ALLURE_REPORTS_DIR
    zip_path = artifacts_root / "allure-report.zip"
    return ReportPaths(results_dir=results_dir, report_dir=report_dir, zip_path=zip_path)
