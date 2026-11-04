"""Locate framework-native reports (BehaveX HTML, pytest junit/html heuristics).

Layout matches :mod:`qasight.engine.executor` output::

    <artifacts>/<cycle>/<stage>/
        run.log
        allure-results/<framework>/
        behave_reports/          # BehaveX -o target (sibling of allure-results)
"""

from __future__ import annotations

import json
import logging
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qasight.reporting.paths import (
    discover_latest_plan_dir,
    plan_artifacts_dir,
    relpath_for_display,
)

logger = logging.getLogger(__name__)


def ensure_behavex_report_html(stage_dir: Path, *, raise_errors: bool = False) -> Path | None:
    """If ``report.json`` exists but ``report.html`` is missing, generate HTML (BehaveX skips HTML when a formatter is set).

    Returns ``report.html`` path when present or successfully generated. Failures are
    logged and the next report root is tried; with ``raise_errors=True`` the first
    failure is re-raised when no root produced HTML, so the caller can record it
    (the executor puts it on ``StageResult.error``).
    """
    stage_dir = stage_dir.expanduser().resolve()
    roots = (
        stage_dir / "behave_reports",
        stage_dir / "allure-results" / "behave_reports",
    )
    first_error: Exception | None = None
    for root in roots:
        html = root / "report.html"
        if html.is_file():
            return html
        js = root / "report.json"
        if not js.is_file():
            continue
        try:
            payload: dict[str, Any] = json.loads(js.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("unreadable BehaveX report %s", js, exc_info=True)
            first_error = first_error or exc
            continue
        root.mkdir(parents=True, exist_ok=True)
        br = str(root.resolve())
        # BehaveX reads paths via :class:`behavex.conf_mgr.ConfigRun` (lowercase keys), not ``os.environ``.
        from behavex.conf_mgr import ConfigRun

        cr = ConfigRun()
        saved_environ = dict(cr.environ)
        try:
            cr.environ["output"] = br
            cr.environ["temp"] = str((root / "temp").resolve())
            cr.environ["logs"] = str((root / "outputs" / "logs").resolve())
            (root / "temp").mkdir(parents=True, exist_ok=True)
            (root / "outputs" / "logs").mkdir(parents=True, exist_ok=True)
            from behavex.outputs.report_html import generate_report

            generate_report(payload)
        except Exception as exc:
            # Broad on purpose: BehaveX's report generator is third-party and raises arbitrary types.
            logger.warning("BehaveX HTML report generation failed under %s", root, exc_info=True)
            first_error = first_error or exc
            continue
        finally:
            cr.environ.clear()
            cr.environ.update(saved_environ)
        if html.is_file():
            return html
    if raise_errors and first_error is not None:
        raise first_error
    return None


@dataclass(frozen=True)
class NativeReportRow:
    """One routine's discoverable native artifact(s)."""

    routine: str
    equipment: str
    open_path: Path | None  # file:// target when set
    open_kind: str  # "html" | "xml" | ""
    notes: str  # e.g. assets folder, or "no raw data"


def resolve_cycle_dir(*, artifacts_root: Path, cycle: str | None) -> Path | None:
    """Return the cycle directory under artifacts, or None if missing."""
    root = artifacts_root.expanduser().resolve()
    if cycle:
        p = plan_artifacts_dir(root, cycle)
        return p if p.is_dir() else None
    latest = discover_latest_plan_dir(root)
    return latest


def load_stage_equipment(cycle_dir: Path) -> dict[str, str]:
    """Map stage (routine) name -> framework string from plan_result.json."""
    plan_result = cycle_dir / "plan_result.json"
    if not plan_result.is_file():
        return {}
    try:
        data: dict[str, Any] = json.loads(plan_result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    stages = data.get("stages")
    if not isinstance(stages, list):
        return {}
    out: dict[str, str] = {}
    for item in stages:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        fw = str(item.get("framework") or "").strip()
        if name and fw:
            out[name] = fw
    return out


def _stage_dirs(cycle_dir: Path) -> list[Path]:
    """Immediate subdirs that look like a stage (run.log or allure-results)."""
    out: list[Path] = []
    try:
        for child in sorted(cycle_dir.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            if (child / "run.log").is_file() or (child / "allure-results").is_dir():
                out.append(child)
    except OSError:
        pass
    return out


def _infer_equipment(stage_dir: Path, routine_name: str, from_json: dict[str, str]) -> str:
    if routine_name in from_json:
        return from_json[routine_name]
    # Fallback: allure-results subdir names, prefer behavex if behave_reports exists
    ar = stage_dir / "allure-results"
    if (stage_dir / "behave_reports").is_dir() or (
        stage_dir / "allure-results" / "behave_reports"
    ).is_dir():
        return "behavex"
    if ar.is_dir():
        try:
            subs = [p.name for p in ar.iterdir() if p.is_dir()]
        except OSError:
            subs = []
        if "behavex" in subs:
            return "behavex"
        if "pytest" in subs:
            return "pytest"
        if "behave" in subs:
            return "behave"
        if subs:
            return subs[0]
    return "unknown"


def _behavex_candidates(stage_dir: Path) -> tuple[Path | None, Path | None]:
    """Return (report_html, behavex_images_dir) if they exist."""
    ensure_behavex_report_html(stage_dir)
    for br in (stage_dir / "behave_reports", stage_dir / "allure-results" / "behave_reports"):
        report = br / "report.html"
        if report.is_file():
            images = br / "behavex_images"
            img_dir = images if images.is_dir() else None
            return report, img_dir
    legacy = stage_dir / "behavex-output" / "report.html"
    if legacy.is_file():
        return legacy, None
    br = stage_dir / "behave_reports"
    images = br / "behavex_images"
    img_dir = images if images.is_dir() else None
    return None, img_dir


def _pytest_candidates(stage_dir: Path) -> tuple[Path | None, str]:
    """Shallow search for junit/html outside allure-results."""
    try:
        for p in sorted(stage_dir.rglob("*")):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(stage_dir)
            except ValueError:
                continue
            parts = rel.parts
            if parts and parts[0] == "allure-results":
                continue
            name = p.name.lower()
            if name.endswith(".html"):
                return p, "html"
            if name.startswith("junit") and name.endswith(".xml"):
                return p, "xml"
            if name.endswith(".xml") and "junit" in name:
                return p, "xml"
    except OSError:
        pass
    return None, ""


def native_row_for_stage(stage_dir: Path, equipment: str) -> NativeReportRow:
    routine = stage_dir.name
    eq = equipment.lower().strip()
    if eq == "behavex":
        html, images = _behavex_candidates(stage_dir)
        if html is not None:
            return NativeReportRow(
                routine=routine, equipment=eq, open_path=html, open_kind="html", notes=""
            )
        if images is not None:
            return NativeReportRow(
                routine=routine,
                equipment=eq,
                open_path=None,
                open_kind="",
                notes=f"assets: {relpath_for_display(images)}",
            )
        return NativeReportRow(
            routine=routine, equipment=eq, open_path=None, open_kind="", notes="no raw data"
        )
    if eq == "pytest":
        path, kind = _pytest_candidates(stage_dir)
        if path is not None:
            return NativeReportRow(
                routine=routine, equipment=eq, open_path=path, open_kind=kind, notes=""
            )
        return NativeReportRow(
            routine=routine, equipment=eq, open_path=None, open_kind="", notes="no raw data"
        )
    if eq == "behave":
        return NativeReportRow(
            routine=routine,
            equipment=eq,
            open_path=None,
            open_kind="",
            notes="no native HTML (Allure-only in this stack)",
        )
    return NativeReportRow(
        routine=routine, equipment=eq, open_path=None, open_kind="", notes="unknown equipment"
    )


def list_native_rows(*, cycle_dir: Path) -> list[NativeReportRow]:
    """All stages under a cycle with native discovery rows."""
    equipment_map = load_stage_equipment(cycle_dir)
    rows: list[NativeReportRow] = []
    for stage_dir in _stage_dirs(cycle_dir):
        routine = stage_dir.name
        eq = _infer_equipment(stage_dir, routine, equipment_map)
        rows.append(native_row_for_stage(stage_dir, eq))
    return rows


def find_stage_dir(cycle_dir: Path, routine: str) -> Path | None:
    """Return stage directory for routine name, or None."""
    p = (cycle_dir / routine).resolve()
    if p.is_dir() and ((p / "run.log").is_file() or (p / "allure-results").is_dir()):
        return p
    return None


def open_native_uri(path: Path) -> bool:
    """Open a file or directory URI in the default browser."""
    uri = path.expanduser().resolve().as_uri()
    return bool(webbrowser.open(uri))
