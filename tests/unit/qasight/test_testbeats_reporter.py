"""TestBeatsReporter.publish: preview, webhook sends and failures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from qasight.reporting.collector import CollectedResults, StageCollection
from qasight.reporting.reporters import testbeats_reporter
from qasight.reporting.reporters.base import ReportContext


def _results(tmp_path: Path) -> CollectedResults:
    stage_dir = tmp_path / "plan-a" / "stage1" / "allure-results" / "pytest"
    stage_dir.mkdir(parents=True)
    (stage_dir / "t1-result.json").write_text(
        json.dumps({"name": "test_alpha", "status": "passed", "start": 1000, "stop": 1500}),
        encoding="utf-8",
    )
    (stage_dir / "t2-result.json").write_text(
        json.dumps({"name": "test_beta", "status": "failed", "start": 2000, "stop": 3000}),
        encoding="utf-8",
    )
    return CollectedResults(
        artifacts_root=tmp_path,
        stages=[
            StageCollection(
                plan="plan-a",
                stage="stage1",
                framework="pytest",
                results_dir=stage_dir,
                log_path=None,
            )
        ],
    )


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def _ctx(tmp_path: Path) -> ReportContext:
    return ReportContext(artifacts_root=tmp_path, plan_name="plan-a")


def test_reporter_type() -> None:
    assert testbeats_reporter.TestBeatsReporter().reporter_type == "testbeats"


def test_no_stages_is_not_ok(tmp_path: Path) -> None:
    empty = CollectedResults(artifacts_root=tmp_path, stages=[])
    outcome = testbeats_reporter.TestBeatsReporter().publish(results=empty, context=_ctx(tmp_path))
    assert not outcome.ok
    assert "no results" in outcome.message


def test_without_webhooks_writes_a_preview_and_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_post(*_a: object, **_k: object) -> None:
        raise AssertionError("must not post without a webhook")

    monkeypatch.setattr(testbeats_reporter.requests, "post", _no_post)
    outcome = testbeats_reporter.TestBeatsReporter(options={"title": "Nightly"}).publish(
        results=_results(tmp_path), context=_ctx(tmp_path)
    )
    out_dir = tmp_path / "reports" / "testbeats"
    assert outcome.ok
    assert "preview" in outcome.message
    preview = json.loads((out_dir / "notification_preview.json").read_text(encoding="utf-8"))
    assert set(preview) == {"slack", "teams"}
    assert (out_dir / "summary.json").is_file()
    assert set(outcome.artifacts) == {
        out_dir / "summary.json",
        out_dir / "notification_preview.json",
    }


def test_sends_to_both_webhooks_and_writes_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[str] = []

    def _post(url: str, json: dict, timeout: int) -> _Response:  # noqa: A002
        posted.append(url)
        assert timeout == 30
        assert json
        return _Response(200)

    monkeypatch.setattr(testbeats_reporter.requests, "post", _post)
    reporter = testbeats_reporter.TestBeatsReporter(
        options={
            "slack_webhook": " https://slack.test/hook ",
            "teams_webhook": "https://teams.test",
        }
    )
    outcome = reporter.publish(results=_results(tmp_path), context=_ctx(tmp_path))
    out_dir = tmp_path / "reports" / "testbeats"
    assert outcome.ok
    assert outcome.message == "TestBeats notification sent."
    assert posted == ["https://slack.test/hook", "https://teams.test"]
    assert (out_dir / "slack_sent.marker").is_file()
    assert (out_dir / "teams_sent.marker").is_file()


def test_http_error_and_network_error_are_reported_per_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _post(url: str, json: dict, timeout: int) -> _Response:  # noqa: A002, ARG001
        if "slack" in url:
            return _Response(403, "invalid_token")
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(testbeats_reporter.requests, "post", _post)
    reporter = testbeats_reporter.TestBeatsReporter(
        options={"slack_webhook": "https://slack.test", "teams_webhook": "https://teams.test"}
    )
    outcome = reporter.publish(results=_results(tmp_path), context=_ctx(tmp_path))
    assert not outcome.ok
    assert "slack: HTTP 403: invalid_token" in outcome.message
    assert "teams: no route to host" in outcome.message
    assert not (tmp_path / "reports" / "testbeats" / "slack_sent.marker").exists()
