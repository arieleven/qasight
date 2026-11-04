"""ReportPortalReporter.publish: option checks, launch upload and error mapping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from qasight.reporting.collector import CollectedResults, StageCollection
from qasight.reporting.reporters import reportportal_reporter
from qasight.reporting.reporters.base import ReportContext
from qasight.reporting.reporters.reportportal_client import ReportPortalError
from qasight.reporting.reporters.reportportal_reporter import ReportPortalReporter

_OPTIONS = {"endpoint": "https://rp.test/", "project": "demo", "token": "t0ken"}


def _results(tmp_path: Path) -> CollectedResults:
    stage_dir = tmp_path / "plan-a" / "stage1" / "allure-results" / "pytest"
    stage_dir.mkdir(parents=True)
    (stage_dir / "t1-result.json").write_text(
        json.dumps({"name": "test_alpha", "status": "passed", "start": 1000, "stop": 1500}),
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


class _FakeClient:
    calls: list[tuple[str, object]] = []
    fail_with: Exception | None = None

    def __init__(self, endpoint: str, project: str, token: str, *, api_version: str | None):
        type(self).calls.append(("init", (endpoint, project, token, api_version)))

    def validate(self) -> None:
        if type(self).fail_with is not None:
            raise type(self).fail_with

    def report_aggregate(self, aggregate, *, launch_name: str, attributes: list) -> str:  # noqa: ANN001
        type(self).calls.append(("launch", (launch_name, attributes, aggregate.total)))
        return "launch-uuid"

    def dashboard_url(self, launch_uuid: str) -> str:
        return f"https://rp.test/ui/#demo/launches/all/{launch_uuid}"


@pytest.fixture()
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    _FakeClient.calls = []
    _FakeClient.fail_with = None
    monkeypatch.setattr(reportportal_reporter, "ReportPortalClient", _FakeClient)
    return _FakeClient


@pytest.mark.parametrize("missing", ["endpoint", "project", "token"])
def test_missing_required_option_is_not_ok(
    tmp_path: Path, fake_client: type[_FakeClient], missing: str
) -> None:
    options = {k: v for k, v in _OPTIONS.items() if k != missing}
    outcome = ReportPortalReporter(options=options).publish(
        results=_results(tmp_path), context=ReportContext(artifacts_root=tmp_path)
    )
    assert not outcome.ok
    assert "requires endpoint, project, and token" in outcome.message
    assert fake_client.calls == []


def test_no_stages_is_not_ok(tmp_path: Path, fake_client: type[_FakeClient]) -> None:
    outcome = ReportPortalReporter(options=_OPTIONS).publish(
        results=CollectedResults(artifacts_root=tmp_path, stages=[]),
        context=ReportContext(artifacts_root=tmp_path),
    )
    assert not outcome.ok
    assert fake_client.calls == []


def test_uploads_a_launch_named_after_the_plan(
    tmp_path: Path, fake_client: type[_FakeClient]
) -> None:
    options = {**_OPTIONS, "api_version": "auto"}
    outcome = ReportPortalReporter(options=options).publish(
        results=_results(tmp_path),
        context=ReportContext(artifacts_root=tmp_path, plan_name="smoke"),
    )
    assert outcome.ok
    assert "launch-uuid" in outcome.message and "https://rp.test/ui/" in outcome.message
    assert fake_client.calls[0] == ("init", ("https://rp.test", "demo", "t0ken", None))
    name, attributes, total = fake_client.calls[1][1]  # type: ignore[misc]
    assert name == "smoke"
    assert {"key": "plan", "value": "smoke"} in attributes
    assert total == 1
    assert outcome.artifacts == (tmp_path / "reports" / "reportportal" / "summary.json",)


def test_launch_name_option_wins(tmp_path: Path, fake_client: type[_FakeClient]) -> None:
    ReportPortalReporter(options={**_OPTIONS, "launch_name": "nightly"}).publish(
        results=_results(tmp_path), context=ReportContext(artifacts_root=tmp_path, plan_name="p")
    )
    assert fake_client.calls[1][1][0] == "nightly"  # type: ignore[index]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ReportPortalError("401 unauthorized"), "401 unauthorized"),
        (requests.ConnectionError("refused"), "ReportPortal upload failed: refused"),
    ],
)
def test_client_errors_become_a_failed_result(
    tmp_path: Path, fake_client: type[_FakeClient], error: Exception, expected: str
) -> None:
    fake_client.fail_with = error
    outcome = ReportPortalReporter(options=_OPTIONS).publish(
        results=_results(tmp_path), context=ReportContext(artifacts_root=tmp_path)
    )
    assert not outcome.ok
    assert outcome.message == expected
