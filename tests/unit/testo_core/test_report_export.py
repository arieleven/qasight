"""Report files (testo_core/services/report_export.py, report_office.py, report_email.py),
``testo export``, ``GET /api/v1/report/export`` and ``testo notify --digest --to email``."""

from __future__ import annotations

import io
import json
from email import message_from_bytes
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testo_core.cli.app import app
from testo_core.services import manager_report, report_email, report_export
from testo_core.services.manager_report import (
    CycleRow,
    Finding,
    Kpis,
    ManagerReport,
    NamedTest,
    TrendPoint,
)

END = 1_761_955_200.0  # 2025-11-01


def _report(**overrides: object) -> ManagerReport:
    base = dict(
        title="Weekly test report",
        period="week",
        days=7,
        start=END - 7 * 86400,
        end=END,
        generated_at=END,
        scope=[],
        summary=["12 runs across 2 cycles; 10 green (83%).", "Pass rate <rose>."],
        kpis=Kpis(
            health_score=84,
            health_grade="B",
            pass_rate=98.5,
            prev_pass_rate=97.0,
            runs=12,
            prev_runs=9,
            green=10,
            prev_green=6,
            coverage_pct=None,
            prev_coverage_pct=None,
            cycles=2,
            failing_tests=1,
            newly_flaky=1,
            fixed=1,
        ),
        trend=[
            TrendPoint("2025-10-31", 5, 4, 97.5),
            TrendPoint("2025-11-01", 7, 6, None),
        ],
        cycles=[
            CycleRow("api", 8, 7, 99.1, 98.0, 0, 1, 1, 0, 90),
            CycleRow("web, e2e", 4, 3, 96.0, None, 1, 0, 0, 1, None),
        ],
        still_failing=[NamedTest("web, e2e", "test_checkout")],
        newly_flaky=[NamedTest("api", "test_login")],
        fixed=[NamedTest("api", "test_cart")],
        slower=[NamedTest("web, e2e", "test_search", "0.20s → 0.90s")],
        findings=[Finding("web, e2e", "web is red", "1 test failing.")],
    )
    base.update(overrides)
    return ManagerReport(**base)  # type: ignore[arg-type]


def _ui(tmp_path: Path) -> Path:
    root = tmp_path / "ui"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        "<!doctype html><html><head>"
        '<link rel="preconnect" href="https://fonts.googleapis.com" />'
        '<link href="https://fonts.googleapis.com/css2?family=X" rel="stylesheet" />'
        "<title>Testosterone</title>"
        '<script type="module" crossorigin src="/assets/app.js"></script>'
        '<link rel="stylesheet" crossorigin href="/assets/app.css">'
        '</head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (root / "assets" / "app.js").write_text('console.log("</script>", "\\\\1");', encoding="utf-8")
    (root / "assets" / "app.css").write_text("body{color:red}", encoding="utf-8")
    return root


def test_json_and_csv() -> None:
    report = _report()
    assert json.loads(report_export.render_json(report))["kpis"]["runs"] == 12
    text = report_export.render_csv(report).decode("utf-8")
    assert text.startswith("﻿Cycle,Runs,Green runs,Pass rate %")
    assert '\r\n"web, e2e",4,3,96.0,,1,0,0,1,\r\n' in text
    assert report_export.file_name(report, "pdf") == "weekly-test-report-2025-11-01.pdf"


def test_single_file_html_inlines_the_ui_and_bakes_in_the_report(tmp_path: Path) -> None:
    report = _report(title="Q4 <check-in>")
    page = report_export.render_html(report, "Acme", ui=_ui(tmp_path)).decode("utf-8")
    assert "fonts.googleapis.com" not in page
    assert "/assets/" not in page
    assert "<style>body{color:red}</style>" in page
    # The bundle's own "</script>" can't end the inline block; backslashes survive as they are.
    assert 'console.log("<\\/script>", "\\\\1");' in page
    assert page.index("window.__TESTO_REPORT__") < page.index('<script type="module">')
    baked = page.split("window.__TESTO_REPORT__ = ", 1)[1].split(";</script>", 1)[0]
    assert "<" not in baked
    data = json.loads(baked)
    assert data["brand"] == "Acme" and data["report"]["summary"][1] == "Pass rate <rose>."
    assert "<title>Q4 &lt;check-in&gt;</title>" in page


def test_html_without_a_ui_build_says_how_to_get_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_export, "ui_dir", lambda: None)
    with pytest.raises(report_export.ExportUnavailable, match="No built web UI"):
        report_export.render_html(_report())


def test_pdf_without_playwright_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def no_playwright(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("playwright"):
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_playwright)
    with pytest.raises(report_export.ExportUnavailable, match=r"testo-core\[export\]"):
        report_export.render_pdf(_report())


def test_excel_workbook() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    from testo_core.services import report_office

    wb = openpyxl.load_workbook(io.BytesIO(report_office.render_xlsx(_report(), "Acme")))
    assert wb.sheetnames == ["Summary", "Trend", "Cycles", "Tests", "Findings"]
    assert wb["Summary"]["A1"].value == "Weekly test report"
    assert wb["Summary"]["B4"].value == "84"
    cycles = list(wb["Cycles"].iter_rows(values_only=True))
    assert cycles[0][0] == "Cycle" and cycles[2][0] == "web, e2e" and cycles[2][9] is None
    tests = list(wb["Tests"].iter_rows(values_only=True))
    assert ("Got slower", "web, e2e", "test_search", "0.20s → 0.90s") in tests
    assert len(wb["Trend"]._charts) == 1


def test_powerpoint_deck_has_native_charts() -> None:
    pptx = pytest.importorskip("pptx")
    from testo_core.services import report_office

    deck = pptx.Presentation(io.BytesIO(report_office.render_pptx(_report(), "Acme")))
    slides = list(deck.slides)
    assert len(slides) == 6
    texts = [
        " ".join(s.text_frame.text for s in slide.shapes if s.has_text_frame) for slide in slides
    ]
    assert "Weekly test report" in texts[0] and "ACME" in texts[0]
    assert "84" in texts[1] and "+1.5 pts" in texts[1]
    charts = [s for s in slides[2].shapes if s.has_chart]
    assert len(charts) == 2
    assert list(charts[0].chart.plots[0].categories) == ["10-31", "11-01"]
    tables = [s.table for s in slides[3].shapes if s.has_table]
    assert tables[0].cell(2, 0).text == "web, e2e"


def test_export_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(manager_report, "build_report", lambda **kw: _report())
    out = tmp_path / "r.json"
    result = CliRunner().invoke(app, ["export", "json", "-o", str(out), "--period", "month"])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["title"] == "Weekly test report"
    assert "12 runs across 2 cycles" in result.output

    bad = CliRunner().invoke(app, ["export", "docx"])
    assert bad.exit_code == 2 and "pdf / png / html" in bad.output

    def unavailable(report: ManagerReport, brand: str) -> bytes:
        raise report_export.ExportUnavailable("PDF and PNG need Playwright")

    monkeypatch.setattr(report_export, "render_pdf", unavailable)
    missing = CliRunner().invoke(app, ["export", "pdf", "-o", str(tmp_path / "r.pdf")])
    assert missing.exit_code == 2 and "need Playwright" in missing.output


def test_export_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from testo_api.main import create_app

    monkeypatch.setattr("testo_api.routes.report.build_report", lambda **kw: _report())
    client = TestClient(create_app())
    resp = client.get("/api/v1/report/export?format=csv&period=week&cycle=api")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="weekly-test-report-2025-11-01.csv"' in resp.headers["content-disposition"]

    def unavailable(report: ManagerReport, brand: str) -> bytes:
        raise report_export.ExportUnavailable("PDF and PNG need Playwright")

    monkeypatch.setattr(report_export, "render_pdf", unavailable)
    refused = client.get("/api/v1/report/export?format=pdf")
    assert refused.status_code == 501
    assert "need Playwright" in refused.text
    assert client.get("/api/v1/report/export?format=docx").status_code == 422


def test_email_message_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = report_email.build_message(
        _report(),
        to=["boss@example.com"],
        sender="qa@example.com",
        brand="Acme",
        attachment=("r.pdf", "application/pdf", b"%PDF-1.7"),
        url="https://testo.example/report",
    )
    parsed = message_from_bytes(msg.as_bytes())
    assert parsed["To"] == "boss@example.com"
    parts = {p.get_content_type(): p for p in parsed.walk()}
    html = parts["text/html"].get_payload(decode=True).decode()
    assert "Pass rate &lt;rose&gt;." in html and "ACME" in html
    assert "https://testo.example/report" in parts["text/plain"].get_payload(decode=True).decode()
    assert parts["application/pdf"].get_filename() == "r.pdf"

    for key in ("TESTO_SMTP_HOST", "TESTO_SMTP_FROM", "TESTO_SMTP_USER", "TESTO_SMTP_PORT"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(report_email.EmailError, match="TESTO_SMTP_HOST"):
        report_email.settings_from_env()
    monkeypatch.setenv("TESTO_SMTP_HOST", "smtp.example")
    monkeypatch.setenv("TESTO_SMTP_USER", "qa@example.com")
    monkeypatch.setenv("TESTO_SMTP_SECURITY", "ssl")
    settings = report_email.settings_from_env()
    assert (settings.port, settings.sender) == (465, "qa@example.com")
    monkeypatch.setenv(report_email.RECIPIENTS_ENV, "a@x.io, b@x.io,a@x.io")
    assert report_email.recipients([]) == ["a@x.io", "b@x.io"]


def test_notify_digest_by_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_report, "build_report", lambda **kw: _report())
    sent: list[object] = []
    monkeypatch.setattr(report_email, "send", lambda msg, settings: sent.append((msg, settings)))
    monkeypatch.setattr(report_export, "render_pdf", lambda report, brand: b"%PDF-1.7")
    monkeypatch.setenv("TESTO_SMTP_HOST", "smtp.example")
    monkeypatch.setenv("TESTO_SMTP_FROM", "qa@example.com")

    result = CliRunner().invoke(
        app, ["notify", "--digest", "week", "--to", "email", "--email-to", "boss@example.com"]
    )
    assert result.exit_code == 0, result.output
    ((msg, settings),) = sent  # type: ignore[misc]
    assert msg["To"] == "boss@example.com" and settings.host == "smtp.example"
    assert [p.get_filename() for p in msg.iter_attachments()] == [
        "weekly-test-report-2025-11-01.pdf"
    ]

    dry = CliRunner().invoke(
        app,
        [
            "notify",
            "--digest",
            "week",
            "--to",
            "email",
            "--email-to",
            "x@y.z",
            "--attach",
            "none",
            "--dry-run",
        ],
    )
    assert dry.exit_code == 0 and "email (dry run): to x@y.z" in dry.output

    bad = CliRunner().invoke(
        app, ["notify", "--digest", "week", "--to", "email", "--to", "fax", "--email-to", "x@y.z"]
    )
    assert bad.exit_code == 2

    monkeypatch.delenv(report_email.RECIPIENTS_ENV, raising=False)
    nobody = CliRunner().invoke(app, ["notify", "--digest", "week", "--to", "email"])
    assert nobody.exit_code == 2 and "no recipients" in nobody.output
