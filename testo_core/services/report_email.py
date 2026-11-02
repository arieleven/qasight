"""Email the manager report: an HTML summary in the body and the report attached (PDF by default).

Chat webhooks (Slack, Teams) can't carry a file, so ``testo notify --digest week
--to email`` sends the report by email instead, for people who never open the
dashboard. Run it from cron or a scheduled CI job, like the chat digest.

SMTP settings come from the environment:

``TESTO_SMTP_HOST`` (required), ``TESTO_SMTP_PORT`` (default 587, or 465 with
``TESTO_SMTP_SECURITY=ssl``), ``TESTO_SMTP_SECURITY`` (``starttls`` default,
``ssl`` or ``none``), ``TESTO_SMTP_USER`` / ``TESTO_SMTP_PASSWORD`` (optional),
``TESTO_SMTP_FROM`` (default: the user), and ``TESTO_REPORT_EMAIL_TO``
(comma-separated recipients, when ``--email-to`` isn't given).
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape

from testo_core.services.manager_report import ManagerReport
from testo_core.services.report_office import kpi_cards, period_range, scope_text

RECIPIENTS_ENV = "TESTO_REPORT_EMAIL_TO"


class EmailError(RuntimeError):
    """The email could not be sent (missing settings or an SMTP error)."""


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str  # starttls | ssl | none
    user: str | None
    password: str | None
    sender: str


def settings_from_env() -> SmtpSettings:
    host = (os.environ.get("TESTO_SMTP_HOST") or "").strip()
    if not host:
        raise EmailError(
            "not set: TESTO_SMTP_HOST (and usually TESTO_SMTP_USER, TESTO_SMTP_PASSWORD)"
        )
    security = (os.environ.get("TESTO_SMTP_SECURITY") or "starttls").strip().lower()
    if security not in ("starttls", "ssl", "none"):
        raise EmailError("TESTO_SMTP_SECURITY takes starttls, ssl or none")
    raw_port = (os.environ.get("TESTO_SMTP_PORT") or "").strip()
    try:
        port = int(raw_port) if raw_port else (465 if security == "ssl" else 587)
    except ValueError:
        raise EmailError(f"TESTO_SMTP_PORT is not a number: {raw_port}") from None
    user = (os.environ.get("TESTO_SMTP_USER") or "").strip() or None
    sender = (os.environ.get("TESTO_SMTP_FROM") or "").strip() or user
    if not sender:
        raise EmailError("not set: TESTO_SMTP_FROM (the From address)")
    return SmtpSettings(
        host=host,
        port=port,
        security=security,
        user=user,
        password=os.environ.get("TESTO_SMTP_PASSWORD") or None,
        sender=sender,
    )


def recipients(given: list[str]) -> list[str]:
    raw = given or (os.environ.get(RECIPIENTS_ENV) or "").split(",")
    out = [r.strip() for r in raw if r.strip()]
    if not out:
        raise EmailError(f"no recipients: pass --email-to or set {RECIPIENTS_ENV}")
    return list(dict.fromkeys(out))


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{v:.1f}%"


def html_body(report: ManagerReport, *, brand: str, url: str | None = None) -> str:
    """A plain, inline-styled summary that renders the same in Gmail, Outlook and Apple Mail."""
    cell = "padding:6px 10px;border-bottom:1px solid #e4e7ec;text-align:left"
    cards = "".join(
        f'<td style="padding:12px 14px;background:#f2f4f7;border-radius:8px;width:25%">'
        f'<div style="font-size:11px;color:#475467;text-transform:uppercase;letter-spacing:.05em">'
        f"{escape(label)}</div>"
        f'<div style="font-size:26px;font-weight:700;color:#{colour}">{escape(value)}</div>'
        f'<div style="font-size:12px;color:#475467">{escape(note)}</div></td>'
        for label, value, note, colour in kpi_cards(report)
    )
    summary = "".join(f"<li>{escape(line)}</li>" for line in report.summary)
    rows = "".join(
        f'<tr><td style="{cell}">{escape(c.cycle)}</td><td style="{cell}">{c.runs}</td>'
        f'<td style="{cell}">{c.green}</td><td style="{cell}">{_pct(c.pass_rate)}</td>'
        f'<td style="{cell}">{c.failing}</td><td style="{cell}">{c.newly_flaky}</td>'
        f'<td style="{cell}">{"–" if c.health_score is None else c.health_score}</td></tr>'
        for c in report.cycles
    )
    heads = "".join(
        f'<th style="{cell};color:#475467;font-size:12px">{h}</th>'
        for h in ("Cycle", "Runs", "Green", "Pass rate", "Failing", "New flaky", "Health")
    )
    attention = "".join(
        f"<li><code>{escape(t.name)}</code> ({escape(t.cycle)}): {label}</li>"
        for label, items in (
            ("still failing", report.still_failing),
            ("turned flaky", report.newly_flaky),
        )
        for t in items
    )
    link = (
        f'<p><a href="{escape(url, quote=True)}" style="color:#1d4ed8">Open the report</a></p>'
        if url
        else ""
    )
    return (
        '<div style="font-family:Inter,Segoe UI,Helvetica,Arial,sans-serif;color:#101828;'
        'max-width:720px">'
        f'<div style="font-size:12px;font-weight:700;letter-spacing:.2em;color:#1d4ed8">'
        f"{escape(brand.upper())}</div>"
        f'<h1 style="margin:4px 0;font-size:24px">{escape(report.title)}</h1>'
        f'<div style="color:#475467;font-size:14px">{escape(period_range(report))} · '
        f"{escape(scope_text(report))}</div>"
        f'<table style="width:100%;border-spacing:8px;margin:16px -8px"><tr>{cards}</tr></table>'
        f'<ul style="font-size:15px;line-height:1.6">{summary}</ul>'
        + (
            f'<table style="border-collapse:collapse;font-size:14px;width:100%">'
            f"<tr>{heads}</tr>{rows}</table>"
            if rows
            else ""
        )
        + (
            f'<h3 style="margin-top:20px">Needs attention</h3><ul>{attention}</ul>'
            if attention
            else ""
        )
        + link
        + '<p style="color:#98a2b3;font-size:12px">The full report is attached.</p></div>'
    )


def text_body(report: ManagerReport, *, url: str | None = None) -> str:
    lines = [report.title, f"{period_range(report)} · {scope_text(report)}", ""]
    lines += [f"- {line}" for line in report.summary]
    if url:
        lines += ["", f"Open the report: {url}"]
    lines += ["", "The full report is attached."]
    return "\n".join(lines)


def build_message(
    report: ManagerReport,
    *,
    to: list[str],
    sender: str,
    brand: str,
    attachment: tuple[str, str, bytes] | None,
    url: str | None = None,
) -> EmailMessage:
    """The email: plain text, an HTML alternative, and ``attachment`` (name, media type, bytes)."""
    msg = EmailMessage()
    msg["Subject"] = f"{report.title}: {period_range(report)}"
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.set_content(text_body(report, url=url))
    msg.add_alternative(html_body(report, brand=brand, url=url), subtype="html")
    if attachment is not None:
        name, media_type, data = attachment
        maintype, _, subtype = media_type.split(";", 1)[0].partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg


def send(msg: EmailMessage, settings: SmtpSettings) -> None:
    try:
        if settings.security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=30, context=ssl.create_default_context()
            )
        else:
            server = smtplib.SMTP(settings.host, settings.port, timeout=30)
        with server:
            if settings.security == "starttls":
                server.starttls(context=ssl.create_default_context())
            if settings.user and settings.password:
                server.login(settings.user, settings.password)
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailError(
            f"could not send the email via {settings.host}:{settings.port}: {exc}"
        ) from exc
