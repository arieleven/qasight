from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from qasight.services import schedules

MONDAY_9 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def test_from_env_picks_the_channels_that_are_set_up() -> None:
    env = {"TESTO_SCHEDULE_DIGEST": "week", "TESTO_SLACK_WEBHOOK_URL": "https://h"}
    s = schedules.from_env(env)
    assert s is not None and s.channels == ("slack",) and s.hour == 8
    assert schedules.from_env({}) is None
    assert schedules.from_env({"TESTO_SCHEDULE_DIGEST": "week"}) is None  # nowhere to send
    assert (
        schedules.from_env({"TESTO_SCHEDULE_DIGEST": "month", "TESTO_SCHEDULE_TO": "slack"}) is None
    )


def test_period_key_is_due_on_monday_after_the_hour() -> None:
    week = schedules.Schedule(period="week", channels=("slack",))
    assert schedules.period_key(week, MONDAY_9) == "2026-W46"
    assert schedules.period_key(week, MONDAY_9.replace(hour=7)) is None
    assert schedules.period_key(week, MONDAY_9.replace(day=10)) is None  # Tuesday
    day = schedules.Schedule(period="day", channels=("slack",))
    assert schedules.period_key(day, MONDAY_9.replace(day=10)) == "2026-11-10"


def test_each_period_is_sent_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sends: list[str] = []
    monkeypatch.setattr(schedules, "send_digest", lambda s: sends.append(s.period) or ["slack"])
    week = schedules.Schedule(period="week", channels=("slack",))
    state = tmp_path / "state.json"
    assert schedules.tick(week, now=MONDAY_9, state=state) == "2026-W46"
    assert schedules.tick(week, now=MONDAY_9.replace(hour=10), state=state) is None
    assert sends == ["week"]


def test_send_digest_posts_to_each_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    from qasight.services import run_alerts, run_digest

    posted: list[str] = []
    monkeypatch.setenv("TESTO_SLACK_WEBHOOK_URL", "https://hooks/s")
    monkeypatch.setattr(
        run_digest, "build_digest", lambda period: type("D", (), {"cycles": (1,)})()
    )
    monkeypatch.setattr(
        run_digest, "build_payload", lambda channel, report, url=None: {"c": channel}
    )
    monkeypatch.setattr(run_alerts, "post", lambda url, payload: posted.append(url))
    sent = schedules.send_digest(schedules.Schedule(period="week", channels=("slack",)))
    assert sent == ["slack"] and posted == ["https://hooks/s"]
