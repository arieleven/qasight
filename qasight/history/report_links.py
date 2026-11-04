"""Which HTML reports exist for a run, and the URLs the UI should open."""

from __future__ import annotations

import os

from qasight import paths


def local_report_links(run_id: str) -> dict[str, str]:
    """Links (relative to the API's ``/`` mount) to reports under ``static/history/<run_id>/``.

    Keys are framework names as the reporters wrote them (``allure_reports/<framework>``),
    ``extent``, and ``<framework>-native`` for a framework's own report.
    """
    base = paths.STATIC_HISTORY_ROOT / run_id
    links: dict[str, str] = {}

    # Scanned rather than matched against a fixed list: adapters name their
    # subdirectory after the stage's equipment (e.g. "behave").
    allure_reports_dir = base / "allure_reports"
    if allure_reports_dir.is_dir():
        for fw_dir in sorted(allure_reports_dir.iterdir()):
            if fw_dir.is_dir() and (fw_dir / "index.html").is_file():
                links[fw_dir.name] = f"history/{run_id}/allure_reports/{fw_dir.name}/index.html"

    if (base / "extent_report" / "index.html").is_file():
        links["extent"] = f"history/{run_id}/extent_report/index.html"

    # A framework's own report (e.g. BehaveX's dashboard); the suffix keeps it
    # apart from the Allure view of the same framework.
    native_reports_dir = base / "native_reports"
    if native_reports_dir.is_dir():
        for fw_dir in sorted(native_reports_dir.iterdir()):
            if fw_dir.is_dir() and (fw_dir / "index.html").is_file():
                links[f"{fw_dir.name}-native"] = (
                    f"history/{run_id}/native_reports/{fw_dir.name}/index.html"
                )
    return links


def allure_report_url_for_run(run_id: str) -> str:
    """URL of the run's Allure bundle on the hosted Allure server (``ALLURE_SERVER_URL``)."""
    base = (os.getenv("ALLURE_SERVER_URL") or "http://localhost:5050").rstrip("/")
    return f"{base}/reports/{run_id}/index.html"
