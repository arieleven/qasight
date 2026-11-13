"""Scheduled sends on the team server: the digest and the manager report email.

Before this, the digest (``qasight notify --digest``) and the manager report by
email needed a cron job or a scheduled CI workflow next to the server. The
server already runs a timer (history retention), so it sends them itself:

* ``TESTO_SCHEDULE_DIGEST``: ``week`` (Mondays) or ``day`` (every day), sent at or
  after ``TESTO_SCHEDULE_HOUR`` (UTC, default 8).
* ``TESTO_SCHEDULE_TO``: where, comma-separated: ``slack``, ``teams``, ``email``.
  By default every channel that is set up: a webhook (``TESTO_SLACK_WEBHOOK_URL``,
  ``TESTO_TEAMS_WEBHOOK_URL``), and email when ``TESTO_REPORT_EMAIL_TO`` and the
  ``TESTO_SMTP_*`` settings are set. Email is the manager report with a PDF.
* ``TESTO_UI_URL``: the server's address, for the "Open QASight" link.

Each period is sent once: the last one sent is kept in a small JSON file next to
the run history, so a restart doesn't send it again. Run one server replica with
schedules on; two would both send.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DIGEST_ENV = "TESTO_SCHEDULE_DIGEST"
TO_ENV = "TESTO_SCHEDULE_TO"
HOUR_ENV = "TESTO_SCHEDULE_HOUR"
UI_URL_ENV = "TESTO_UI_URL"
CHECK_INTERVAL_S = 900


@dataclass(frozen=True)
class Schedule:
    period: str  # "week" | "day"
    channels: tuple[str, ...]
    hour: int = 8
    url: str | None = None


def from_env(env: Mapping[str, str] | None = None) -> Schedule | None:
    """The configured schedule, or ``None`` when ``TESTO_SCHEDULE_DIGEST`` is unset or invalid."""
    env = os.environ if env is None else env
    period = (env.get(DIGEST_ENV) or "").strip().lower()
    if period not in {"week", "day"}:
        if period:
            logger.warning("%s=%r: use week or day; no scheduled digest", DIGEST_ENV, period)
        return None
    raw = (env.get(TO_ENV) or "").strip()
    if raw:
        channels = tuple(dict.fromkeys(c.strip().lower() for c in raw.split(",") if c.strip()))
    else:
        found = [
            c
            for c in ("slack", "teams")
            if (env.get(f"TESTO_{c.upper()}_WEBHOOK_URL") or "").strip()
        ]
        if (env.get("TESTO_REPORT_EMAIL_TO") or "").strip() and (
            env.get("TESTO_SMTP_HOST") or ""
        ).strip():
            found.append("email")
        channels = tuple(found)
    bad = [c for c in channels if c not in {"slack", "teams", "email"}]
    if bad or not channels:
        logger.warning(
            "%s: no usable channel (%s); no scheduled digest", TO_ENV, ", ".join(bad) or "none set"
        )
        return None
    try:
        hour = min(23, max(0, int((env.get(HOUR_ENV) or "8").strip())))
    except ValueError:
        hour = 8
    return Schedule(
        period=period,
        channels=channels,
        hour=hour,
        url=(env.get(UI_URL_ENV) or "").strip().rstrip("/") or None,
    )


def period_key(schedule: Schedule, now: datetime) -> str | None:
    """The period that is due at ``now`` (``"2026-W46"`` / ``"2026-11-12"``), or ``None`` yet."""
    if now.hour < schedule.hour:
        return None
    if schedule.period == "week":
        if now.weekday() != 0:  # Monday
            return None
        year, week, _ = now.isocalendar()
        return f"{year}-W{week:02d}"
    return now.date().isoformat()


def _state_path() -> Path:
    from qasight import paths

    return Path(paths.ORCHESTRATOR_ROOT) / ".testo-schedules.json"


def _last_sent(path: Path) -> str | None:
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("digest") or "") or None
    except (OSError, ValueError, AttributeError):
        return None


def _remember(path: Path, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"digest": key}), encoding="utf-8")


def send_digest(schedule: Schedule) -> list[str]:
    """Send the period's digest to each channel now; returns the channels that got it."""
    from qasight.services import run_alerts, run_digest

    sent: list[str] = []
    chat = [c for c in schedule.channels if c in run_alerts.CHANNELS]
    if chat:
        report = run_digest.build_digest(period=schedule.period)
        if report.cycles:
            for channel in chat:
                url = run_alerts.webhook_from_env(channel)  # type: ignore[arg-type]
                if not url:
                    logger.warning("scheduled digest: %s has no webhook set", channel)
                    continue
                payload = run_digest.build_payload(channel, report, url=schedule.url)  # type: ignore[arg-type]
                run_alerts.post(url, payload)
                sent.append(channel)
    if "email" in schedule.channels and _email(schedule):
        sent.append("email")
    return sent


def _email(schedule: Schedule) -> bool:
    from qasight.services import report_email
    from qasight.services.manager_report import build_report
    from qasight.services.report_export import ExportUnavailable, file_name, formats

    report = build_report(period=schedule.period)
    if not report.cycles:
        return False
    settings = report_email.settings_from_env()
    attachment = None
    try:
        spec = formats()["pdf"]
        attachment = (
            file_name(report, spec.extension),
            spec.media_type,
            spec.render(report, "QASight"),
        )
    except ExportUnavailable:
        logger.warning("scheduled report email: PDF unavailable, sent without an attachment")
    msg = report_email.build_message(
        report,
        to=report_email.recipients([]),
        sender=settings.sender,
        brand="QASight",
        attachment=attachment,
        url=schedule.url,
    )
    report_email.send(msg, settings)
    return True


def tick(
    schedule: Schedule, *, now: datetime | None = None, state: Path | None = None
) -> str | None:
    """Send the due period once; returns its key when this call sent it."""
    key = period_key(schedule, now or datetime.now(UTC))
    path = state or _state_path()
    if key is None or _last_sent(path) == key:
        return None
    # Remembered first: a failing channel waits for the next period instead of
    # sending the others again every check.
    _remember(path, key)
    sent = send_digest(schedule)
    logger.info(
        "scheduled %s digest %s sent to %s", schedule.period, key, ", ".join(sent) or "nobody"
    )
    return key
