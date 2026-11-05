"""Slack Block Kit and Microsoft Teams Adaptive Card payloads for TestBeats."""

from __future__ import annotations

from qasight.reporting.allure_results import RunAggregate, format_duration
from qasight.reporting.reporters.base import ReportContext

ACCENT_GREEN = "#2EB67D"
ACCENT_RED = "#E01E5A"


def accent_color(aggregate: RunAggregate) -> str:
    """Green when no failures; red when failed or broken > 0."""
    if aggregate.failed > 0 or aggregate.broken > 0:
        return ACCENT_RED
    return ACCENT_GREEN


def overall_emoji(aggregate: RunAggregate) -> str:
    return ":white_check_mark:" if aggregate.overall_passed else ":x:"


def build_slack_payload(
    aggregate: RunAggregate,
    *,
    context: ReportContext,
    channel: str = "",
    title: str | None = None,
    report_url: str | None = None,
) -> dict:
    """Build Slack incoming-webhook payload with Block Kit + attachment color bar."""
    run_title = title or context.plan_name or context.run_id or "testo-run"
    header = f"{overall_emoji(aggregate)} QASight — {run_title}"
    if channel:
        header = f"[{channel}] {header}"

    fields = [
        {"type": "mrkdwn", "text": f"*Total*\n{aggregate.total}"},
        {"type": "mrkdwn", "text": f"*Passed*\n{aggregate.passed}"},
        {"type": "mrkdwn", "text": f"*Failed*\n{aggregate.failed}"},
        {"type": "mrkdwn", "text": f"*Duration*\n{format_duration(aggregate.duration_ms)}"},
    ]
    if aggregate.broken:
        fields.append({"type": "mrkdwn", "text": f"*Broken*\n{aggregate.broken}"})
    if aggregate.skipped:
        fields.append({"type": "mrkdwn", "text": f"*Skipped*\n{aggregate.skipped}"})

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header, "emoji": True}},
        {"type": "section", "fields": fields[:10]},
    ]

    frameworks = ", ".join(sorted({s.framework for s in aggregate.stages}))
    if frameworks:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"*Frameworks:* {frameworks}"}],
            }
        )

    if report_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View report", "emoji": True},
                        "url": report_url,
                    }
                ],
            }
        )

    payload: dict = {"blocks": blocks}
    payload["attachments"] = [
        {
            "color": accent_color(aggregate),
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"Status: *{'PASSED' if aggregate.overall_passed else 'FAILED'}* "
                            f"({aggregate.passed}/{aggregate.total} passed)"
                        ),
                    },
                }
            ],
        }
    ]
    return payload


def build_teams_payload(
    aggregate: RunAggregate,
    *,
    context: ReportContext,
    channel: str = "",
    title: str | None = None,
    report_url: str | None = None,
) -> dict:
    """Build Teams workflow message wrapper with Adaptive Card v1.4."""
    run_title = title or context.plan_name or context.run_id or "testo-run"
    status_text = "PASSED" if aggregate.overall_passed else "FAILED"
    accent = accent_color(aggregate)

    facts = [
        {"title": "Total", "value": str(aggregate.total)},
        {"title": "Passed", "value": str(aggregate.passed)},
        {"title": "Failed", "value": str(aggregate.failed)},
        {"title": "Broken", "value": str(aggregate.broken)},
        {"title": "Skipped", "value": str(aggregate.skipped)},
        {"title": "Duration", "value": format_duration(aggregate.duration_ms)},
    ]

    body: list[dict] = [
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": " ",
                            "height": "stretch",
                            "style": "emphasis",
                        }
                    ],
                    "style": "emphasis",
                    "backgroundColor": accent,
                    "minHeight": "48px",
                },
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": run_title,
                            "weight": "Bolder",
                            "size": "Large",
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": f"Status: {status_text}",
                            "color": "Good" if aggregate.overall_passed else "Attention",
                            "spacing": "Small",
                        },
                    ],
                    "verticalContentAlignment": "Center",
                },
            ],
        },
        {"type": "FactSet", "facts": facts},
    ]

    if channel:
        body.insert(
            1,
            {
                "type": "TextBlock",
                "text": f"Channel: {channel}",
                "isSubtle": True,
                "spacing": "Small",
            },
        )

    if report_url:
        body.append(
            {
                "type": "ActionSet",
                "actions": [{"type": "Action.OpenUrl", "title": "View report", "url": report_url}],
            }
        )

    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }
