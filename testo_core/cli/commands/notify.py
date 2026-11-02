"""``testo notify`` — send a recorded run to Slack and/or Microsoft Teams."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import typer

from testo_core.engine.exit_codes import EngineExitCode

if TYPE_CHECKING:
    from testo_core.services import run_alerts


def notify(
    to: list[str] = typer.Option(
        ...,
        "--to",
        help="slack or teams; repeat for both. URLs come from $TESTO_SLACK_WEBHOOK_URL "
        "and $TESTO_TEAMS_WEBHOOK_URL. With --digest also email: the manager report by "
        "email ($TESTO_SMTP_*), with the report attached.",
    ),
    cycle: str | None = typer.Option(
        None, "--cycle", help="Latest run of this cycle (with --digest: only this cycle)."
    ),
    run_id: str | None = typer.Option(None, "--run-id", help="A specific run (run-history id)."),
    when: str = typer.Option(
        "failure",
        "--when",
        help="failure: red run or newly flaky test. change: differs from the previous run. "
        "always: every run.",
    ),
    title: str | None = typer.Option(None, "--title", help="Name in the message (default: cycle)."),
    run_url: str | None = typer.Option(
        None,
        "--run-url",
        help="'View run' link, e.g. the CI run that produced it (with --digest: your UI's URL).",
    ),
    digest: str | None = typer.Option(
        None,
        "--digest",
        help="day or week: send a digest of every cycle's runs over that period instead of "
        "one run (newly flaky, fixed, still failing, slower). Run it from cron or a "
        "scheduled CI job.",
    ),
    email_to: list[str] = typer.Option(
        [],
        "--email-to",
        help="With --to email: a recipient; repeat for several (default: $TESTO_REPORT_EMAIL_TO).",
    ),
    attach: str = typer.Option(
        "pdf",
        "--attach",
        help="With --to email: the report file to attach: pdf, pptx, xlsx, html, png or none.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the JSON payloads instead of sending them."
    ),
) -> None:
    """Send the run (failing tests, regressions, newly flaky tests), or a digest, to chat webhooks."""
    import json

    from testo_core.services import run_alerts

    if digest is not None:
        _send_digest(
            to=to,
            period=digest,
            cycle=cycle,
            run_id=run_id,
            url=run_url,
            dry_run=dry_run,
            email_to=email_to,
            attach=attach,
        )
    channels = [c.strip().lower() for c in to]
    bad = [c for c in channels if c not in run_alerts.CHANNELS]
    if bad or when not in run_alerts.WHEN_CHOICES:
        typer.echo(
            f"--to takes {' / '.join(run_alerts.CHANNELS)} and --when takes "
            f"{' / '.join(run_alerts.WHEN_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    targets = cast("list[run_alerts.AlertChannel]", list(dict.fromkeys(channels)))
    urls = _webhooks(targets, dry_run=dry_run)

    alert = run_alerts.load_alert(cycle=cycle.strip() if cycle else None, run_id=run_id)
    if alert is None:
        typer.echo("no recorded run found", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    if not run_alerts.should_send(alert, cast("run_alerts.AlertWhen", when)):
        typer.echo(f"nothing to report (--when {when})", err=True)
        raise typer.Exit(code=0)

    failed = False
    for channel in targets:
        payload = run_alerts.build_payload(channel, alert, title=title, run_url=run_url)
        if dry_run:
            typer.echo(json.dumps({channel: payload}, indent=2, ensure_ascii=False))
            continue
        try:
            run_alerts.post(urls[channel], payload)
        except run_alerts.AlertError as exc:
            typer.echo(f"{channel}: {exc}", err=True)
            failed = True
        else:
            typer.echo(f"{channel}: sent", err=True)
    raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE) if failed else 0)


def _webhooks(
    targets: list[run_alerts.AlertChannel], *, dry_run: bool
) -> dict[run_alerts.AlertChannel, str]:
    from testo_core.services import run_alerts

    urls = {c: run_alerts.webhook_from_env(c) for c in targets}
    missing = [run_alerts.WEBHOOK_ENV[c] for c, url in urls.items() if not url]
    if missing and not dry_run:
        typer.echo(f"not set: {', '.join(missing)}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    return urls


def _send_digest(
    *,
    to: list[str],
    period: str,
    cycle: str | None,
    run_id: str | None,
    url: str | None,
    dry_run: bool,
    email_to: list[str] | None = None,
    attach: str = "pdf",
) -> None:
    import json

    from testo_core.services import run_alerts, run_digest

    channels = [c.strip().lower() for c in to]
    bad = [c for c in channels if c not in (*run_alerts.CHANNELS, "email")]
    if bad or period not in run_digest.PERIODS or run_id:
        typer.echo(
            f"--to takes {' / '.join(run_alerts.CHANNELS)} / email, --digest takes "
            f"{' / '.join(run_digest.PERIODS)}, and --digest doesn't take --run-id",
            err=True,
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    failed = False
    if "email" in channels:
        channels = [c for c in channels if c != "email"]
        failed = not _email_report(
            period=period, cycle=cycle, url=url, to=email_to or [], attach=attach, dry_run=dry_run
        )
        if not channels:
            raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE) if failed else 0)
    targets = cast("list[run_alerts.AlertChannel]", list(dict.fromkeys(channels)))
    urls = _webhooks(targets, dry_run=dry_run)
    report = run_digest.build_digest(
        period=period, cycles=[cycle.strip()] if cycle and cycle.strip() else None
    )
    if not report.cycles:
        typer.echo(f"nothing to report: no runs in the last {period}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE) if failed else 0)
    for channel in targets:
        payload = run_digest.build_payload(channel, report, url=url)
        if dry_run:
            typer.echo(json.dumps({channel: payload}, indent=2, ensure_ascii=False))
            continue
        try:
            run_alerts.post(urls[channel], payload)
        except run_alerts.AlertError as exc:
            typer.echo(f"{channel}: {exc}", err=True)
            failed = True
        else:
            typer.echo(f"{channel}: digest sent", err=True)
    raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE) if failed else 0)


def _email_report(
    *, period: str, cycle: str | None, url: str | None, to: list[str], attach: str, dry_run: bool
) -> bool:
    """Email the manager report for ``period`` with the report attached; False if it failed."""
    from testo_core.services import report_email, run_digest
    from testo_core.services.manager_report import build_report
    from testo_core.services.report_export import ExportUnavailable, file_name, formats

    attach = attach.strip().lower()
    if period not in run_digest.PERIODS or attach not in (
        "pdf",
        "pptx",
        "xlsx",
        "html",
        "png",
        "none",
    ):
        typer.echo(
            "--digest takes day or week, --attach pdf / pptx / xlsx / html / png / none", err=True
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    try:
        recipients = report_email.recipients(to)
        settings = None if dry_run else report_email.settings_from_env()
    except report_email.EmailError as exc:
        typer.echo(f"email: {exc}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from None
    report = build_report(
        period=period, cycles=[cycle.strip()] if cycle and cycle.strip() else None
    )
    if not report.cycles:
        typer.echo(f"email: nothing to report: no runs in the last {period}", err=True)
        return True
    attachment = None
    if attach != "none":
        spec = formats()[attach]
        try:
            attachment = (
                file_name(report, spec.extension),
                spec.media_type,
                spec.render(report, "Testosterone"),
            )
        except ExportUnavailable as exc:
            typer.echo(f"email: {exc}", err=True)
            return False
    msg = report_email.build_message(
        report,
        to=recipients,
        sender=settings.sender if settings else "testo@localhost",
        brand="Testosterone",
        attachment=attachment,
        url=url,
    )
    if settings is None:
        typer.echo(f"email (dry run): to {', '.join(recipients)}: {msg['Subject']}")
        if attachment:
            typer.echo(f"  attached {attachment[0]} ({len(attachment[2]) / 1024:.0f} KB)")
        return True
    try:
        report_email.send(msg, settings)
    except report_email.EmailError as exc:
        typer.echo(f"email: {exc}", err=True)
        return False
    typer.echo(f"email: report sent to {len(recipients)} recipient(s)", err=True)
    return True
