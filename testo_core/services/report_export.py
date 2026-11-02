"""Files from the manager report: JSON, CSV, single-file HTML, PDF and PNG.

Every format starts from the same :class:`~testo_core.services.manager_report.ManagerReport`.
The visual formats don't have a template of their own: the single-file HTML is the
built web UI with its script and styles inlined and the report baked in
(``window.__TESTO_REPORT__``), so it opens offline and looks exactly like the
``/report`` page. PDF and PNG are that HTML rendered by headless Chromium
(Playwright, from the ``export`` extra), the way Grafana, Datadog and Looker
render their dashboard PDFs. Spreadsheet and slides live in
:mod:`testo_core.services.report_office`.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from testo_core.services.manager_report import ManagerReport
from testo_core.ui_bundle import ui_dir

DEFAULT_BRAND = "Testosterone"
# A Chrome or Chromium to use instead of Playwright's own download (e.g. a system browser).
CHROMIUM_ENV = "TESTO_CHROMIUM"
_EXPORT_HINT = "pip install 'testo-core[export]' && playwright install chromium"
_BUILD_HINT = (
    "No built web UI found to draw the report with. A `pip install testo-core[api]` from "
    "PyPI includes one; from a source checkout run `scripts/build_ui_bundle.sh`, or point "
    'TESTO_UI_DIR at a `npm run build` output made with VITE_API_BASE_URL="".'
)


class ExportUnavailable(RuntimeError):
    """A format needs something this install doesn't have (the message says what)."""


@dataclass(frozen=True)
class ExportFormat:
    extension: str
    media_type: str
    render: Callable[[ManagerReport, str], bytes]


def file_name(report: ManagerReport, extension: str) -> str:
    """``weekly-test-report-2026-11-01.pdf``."""
    from datetime import UTC, datetime

    day = datetime.fromtimestamp(report.end, tz=UTC).date().isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", report.title.lower()).strip("-") or "test-report"
    return f"{slug}-{day}.{extension}"


# -- data ---------------------------------------------------------------------------


def render_json(report: ManagerReport, brand: str = DEFAULT_BRAND) -> bytes:  # noqa: ARG001
    return (json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n").encode("utf-8")


CYCLE_COLUMNS = [
    ("cycle", "Cycle"),
    ("runs", "Runs"),
    ("green", "Green runs"),
    ("pass_rate", "Pass rate %"),
    ("prev_pass_rate", "Previous pass rate %"),
    ("failing", "Failing tests"),
    ("newly_flaky", "Newly flaky"),
    ("fixed", "Fixed"),
    ("slower", "Got slower"),
    ("health_score", "Health score"),
]


def render_csv(report: ManagerReport, brand: str = DEFAULT_BRAND) -> bytes:  # noqa: ARG001
    """The per-cycle table, one row per cycle (UTF-8 with a BOM so Excel reads it right)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([label for _, label in CYCLE_COLUMNS])
    for row in report.cycles:
        values = row.__dict__
        writer.writerow(["" if values[key] is None else values[key] for key, _ in CYCLE_COLUMNS])
    return ("﻿" + out.getvalue()).encode("utf-8")


# -- single-file HTML ------------------------------------------------------------------

_FONT_LINKS = re.compile(
    r"<link[^>]+(?:fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>\s*", re.IGNORECASE | re.DOTALL
)
_SCRIPT = re.compile(r'<script type="module"[^>]*\bsrc="([^"]+)"[^>]*></script>')
_TITLE = re.compile(r"<title>.*?</title>", re.DOTALL)
_STYLE = re.compile(r'<link rel="stylesheet"[^>]*\bhref="([^"]+)"[^>]*>')


def _asset(root: Path, href: str) -> str:
    path = (root / href.lstrip("/").split("?", 1)[0]).resolve()
    if root.resolve() not in path.parents:
        raise ExportUnavailable(f"UI build refers to a file outside it: {href}")
    return path.read_text(encoding="utf-8")


def render_html(
    report: ManagerReport, brand: str = DEFAULT_BRAND, *, ui: Path | None = None
) -> bytes:
    """The report as one HTML file: opens offline, prints like the ``/report`` page."""
    root = ui or ui_dir()
    if root is None:
        raise ExportUnavailable(_BUILD_HINT)
    page = (root / "index.html").read_text(encoding="utf-8")
    page = _FONT_LINKS.sub("", page)
    scripts = _SCRIPT.findall(page)
    if not scripts:
        raise ExportUnavailable(f"{root / 'index.html'} has no module script; rebuild the UI.")
    for href in _STYLE.findall(page):
        css = _asset(root, href).replace("</style", "<\\/style")
        page = _replace_first(_STYLE, page, f"<style>{css}</style>")
    data = json.dumps({"report": report.to_dict(), "brand": brand}, ensure_ascii=False)
    # Inside <script>, "</script" and "<!--" would end or confuse the block.
    data = data.replace("<", "\\u003c")
    baked = f"<script>window.__TESTO_REPORT__ = {data};</script>"
    for href in scripts:
        js = _asset(root, href).replace("</script", "<\\/script")
        page = _replace_first(_SCRIPT, page, f'{baked}<script type="module">{js}</script>')
        baked = ""
    page = _replace_first(_TITLE, page, f"<title>{_escape(report.title)}</title>")
    return page.encode("utf-8")


def _replace_first(pattern: re.Pattern[str], text: str, replacement: str) -> str:
    """Replace the first match with ``replacement`` taken literally (no backslash escapes)."""
    match = pattern.search(text)
    return text if match is None else text[: match.start()] + replacement + text[match.end() :]


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# -- PDF and PNG (headless Chromium) -----------------------------------------------------


def _with_page(
    report: ManagerReport, brand: str, capture: bool, work: Callable[..., bytes]
) -> bytes:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        raise ExportUnavailable(f"PDF and PNG need Playwright: {_EXPORT_HINT}") from None
    html = render_html(report, brand)
    with tempfile.TemporaryDirectory(prefix="testo-export-") as tmp:
        path = Path(tmp) / "report.html"
        path.write_bytes(html)
        url = path.as_uri() + ("?capture=1" if capture else "")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    executable_path=os.environ.get(CHROMIUM_ENV) or None,
                    args=["--no-sandbox"] if os.environ.get(CHROMIUM_ENV) else None,
                )
                try:
                    page = browser.new_page(
                        viewport={"width": 1000, "height": 1300},
                        device_scale_factor=2 if capture else 1,
                    )
                    page.goto(url, wait_until="load")
                    page.wait_for_selector("[data-report-ready]", timeout=20_000)
                    return work(page)
                finally:
                    browser.close()
        except PlaywrightError as exc:
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
                raise ExportUnavailable(
                    f"Chromium for Playwright isn't installed: {_EXPORT_HINT}"
                ) from None
            raise ExportUnavailable(f"Could not render the report: {first}") from None


def render_pdf(report: ManagerReport, brand: str = DEFAULT_BRAND) -> bytes:
    """A4 PDF with the same print styles as the ``/report`` page's "Save as PDF"."""

    def work(page) -> bytes:  # noqa: ANN001
        page.emulate_media(media="print")
        return page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"},
        )

    return _with_page(report, brand, False, work)


def render_png(report: ManagerReport, brand: str = DEFAULT_BRAND) -> bytes:
    """The whole report page as one image at 2x, for a slide or a chat message."""

    def work(page) -> bytes:  # noqa: ANN001
        page.set_viewport_size({"width": 1000, "height": 1300})
        return page.locator("[data-report-ready]").screenshot(type="png", scale="device")

    return _with_page(report, brand, True, work)


def formats() -> dict[str, ExportFormat]:
    from testo_core.services import report_office

    return {
        "pdf": ExportFormat("pdf", "application/pdf", render_pdf),
        "png": ExportFormat("png", "image/png", render_png),
        "html": ExportFormat("html", "text/html; charset=utf-8", render_html),
        "pptx": ExportFormat(
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            report_office.render_pptx,
        ),
        "xlsx": ExportFormat(
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            report_office.render_xlsx,
        ),
        "csv": ExportFormat("csv", "text/csv; charset=utf-8", render_csv),
        "json": ExportFormat("json", "application/json", render_json),
    }


FORMAT_NAMES = ("pdf", "png", "html", "pptx", "xlsx", "csv", "json")


def export(report: ManagerReport, fmt: str, *, brand: str = DEFAULT_BRAND) -> bytes:
    """``report`` as ``fmt`` (one of :data:`FORMAT_NAMES`); :class:`ExportUnavailable` if it can't be made here."""
    spec = formats().get(fmt)
    if spec is None:
        raise ValueError(f"format must be one of {', '.join(FORMAT_NAMES)}")
    return spec.render(report, brand)
