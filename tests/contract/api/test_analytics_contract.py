from __future__ import annotations

from fastapi.testclient import TestClient

from qasight.services.delta_models import (
    DeltaComparisonResult,
    DeltaStatusSummary,
    MetricDelta,
    StageDelta,
)
from qasight.services.delta_service import (
    IncompatibleRunDataError,
    InvalidRunIdError,
    RunNotFoundComparisonError,
)
from qasight_api.main import create_app


def _fake_result() -> DeltaComparisonResult:
    metrics = (
        MetricDelta(
            metric_key="total_tests",
            label="Total tests",
            group="reliability",
            current_value=120.0,
            baseline_value=100.0,
            absolute_delta=20.0,
            relative_delta_pct=20.0,
            classification="improvement",
            reason=None,
            direction="higher_is_better",
            unit="tests",
        ),
        MetricDelta(
            metric_key="passed",
            label="Passed tests",
            group="reliability",
            current_value=110.0,
            baseline_value=98.0,
            absolute_delta=12.0,
            relative_delta_pct=12.2449,
            classification="improvement",
            reason=None,
            direction="higher_is_better",
            unit="tests",
        ),
        MetricDelta(
            metric_key="failed",
            label="Failed tests",
            group="reliability",
            current_value=6.0,
            baseline_value=1.0,
            absolute_delta=5.0,
            relative_delta_pct=500.0,
            classification="regression",
            reason=None,
            direction="lower_is_better",
            unit="tests",
        ),
        MetricDelta(
            metric_key="broken",
            label="Broken tests",
            group="reliability",
            current_value=2.0,
            baseline_value=1.0,
            absolute_delta=1.0,
            relative_delta_pct=100.0,
            classification="regression",
            reason=None,
            direction="lower_is_better",
            unit="tests",
        ),
        MetricDelta(
            metric_key="skipped",
            label="Skipped tests",
            group="reliability",
            current_value=2.0,
            baseline_value=0.0,
            absolute_delta=2.0,
            relative_delta_pct=None,
            classification="regression",
            reason="zero_baseline_for_relative",
            direction="lower_is_better",
            unit="tests",
        ),
        MetricDelta(
            metric_key="health_pct",
            label="Health percentage",
            group="reliability",
            current_value=91.5,
            baseline_value=99.0,
            absolute_delta=-7.5,
            relative_delta_pct=-7.5757,
            classification="regression",
            reason=None,
            direction="higher_is_better",
            unit="pct",
        ),
        MetricDelta(
            metric_key="wall_duration_ms",
            label="Wall duration",
            group="performance",
            current_value=1260.0,
            baseline_value=1300.0,
            absolute_delta=-40.0,
            relative_delta_pct=-3.0769,
            classification="improvement",
            reason=None,
            direction="lower_is_better",
            unit="ms",
        ),
        MetricDelta(
            metric_key="metrics_duration_ms",
            label="Metrics duration",
            group="performance",
            current_value=1000.0,
            baseline_value=900.0,
            absolute_delta=100.0,
            relative_delta_pct=11.1111,
            classification="regression",
            reason=None,
            direction="lower_is_better",
            unit="ms",
        ),
        MetricDelta(
            metric_key="avg_case_ms",
            label="Average case duration",
            group="performance",
            current_value=10.2,
            baseline_value=11.5,
            absolute_delta=-1.3,
            relative_delta_pct=-11.3043,
            classification="improvement",
            reason=None,
            direction="lower_is_better",
            unit="ms",
        ),
    )
    return DeltaComparisonResult(
        current_run_id="run-2",
        baseline_run_id="run-1",
        current_test_kind="pytest",
        baseline_test_kind="pytest",
        metrics=metrics,
        status_summary=DeltaStatusSummary(
            regressions=("failed", "broken", "skipped", "health_pct", "metrics_duration_ms"),
            improvements=("total_tests", "passed", "wall_duration_ms", "avg_case_ms"),
            unchanged=(),
            unknown=(),
        ),
        highlights=(
            "Failed tests worsened by 5 tests.",
            "Wall duration improved by 40.00 ms.",
        ),
    )


def test_analytics_delta_contract_success(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "qasight_api.routes.analytics.DeltaComparisonService.compare_runs",
        lambda self, **_: _fake_result(),
    )
    client = TestClient(create_app())
    resp = client.get("/api/v1/analytics/delta?current_run_id=run-2&baseline_run_id=run-1")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload.keys()) == {
        "comparison",
        "metrics",
        "status_summary",
        "highlights",
        "stage_deltas",
    }
    assert payload["comparison"]["current_run_id"] == "run-2"
    assert set(payload["metrics"].keys()) == {"reliability", "performance"}
    assert payload["metrics"]["reliability"]["failed"]["classification"] == "regression"
    assert payload["metrics"]["performance"]["wall_duration_ms"]["classification"] == "improvement"
    assert payload["stage_deltas"] == []


def test_analytics_delta_contract_includes_stage_deltas(monkeypatch) -> None:  # noqa: ANN001
    from dataclasses import replace

    result_with_stages = replace(
        _fake_result(),
        stage_deltas=(
            StageDelta(
                stage_name="pytest-sample",
                framework="pytest",
                baseline_total_tests=10,
                current_total_tests=10,
                baseline_passed=10,
                current_passed=8,
                baseline_health_pct=100.0,
                current_health_pct=80.0,
                health_pct_delta=-20.0,
                classification="regression",
            ),
        ),
    )
    monkeypatch.setattr(
        "qasight_api.routes.analytics.DeltaComparisonService.compare_runs",
        lambda self, **_: result_with_stages,
    )
    client = TestClient(create_app())
    resp = client.get("/api/v1/analytics/delta?current_run_id=run-2&baseline_run_id=run-1")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["stage_deltas"] == [
        {
            "stage_name": "pytest-sample",
            "framework": "pytest",
            "baseline_total_tests": 10,
            "current_total_tests": 10,
            "baseline_passed": 10,
            "current_passed": 8,
            "baseline_health_pct": 100.0,
            "current_health_pct": 80.0,
            "health_pct_delta": -20.0,
            "classification": "regression",
        }
    ]


def test_analytics_delta_cases_contract(monkeypatch) -> None:  # noqa: ANN001
    from qasight.history.views import CompletedRunView
    from qasight.repository.models import RunStatus
    from qasight.services.report_archive_diff import CaseChange

    def _fake_run(*, run_id: str):
        return CompletedRunView(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            created_at=1.0,
            started_at=1.0,
            finished_at=2.0,
            test_kind="pytest",
            returncode=0,
            wall_duration_ms=1000.0,
            metrics_duration_ms=900,
            total_tests=2,
            passed=2,
            failed=0,
            broken=0,
            skipped=0,
            avg_case_ms=100.0,
            health_pct=100.0,
            target_repo="/tmp/repo",
            snapshot_dir="runs/x/artifacts",
            audit_json=None,
        )

    monkeypatch.setattr("qasight_api.routes.analytics.get_run", _fake_run)
    monkeypatch.setattr(
        "qasight_api.routes.analytics.diff_run_snapshots",
        lambda **_: [
            CaseChange(
                key="a",
                name="test_a",
                group="pkg",
                baseline_status="passed",
                current_status="failed",
                kind="regression",
                duration_delta_ms=5,
            )
        ],
    )
    client = TestClient(create_app())
    resp = client.get("/api/v1/analytics/delta/cases?current_run_id=run-2&baseline_run_id=run-1")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["current_run_id"] == "run-2"
    assert payload["baseline_run_id"] == "run-1"
    assert payload["changes"] == [
        {
            "key": "a",
            "name": "test_a",
            "group": "pkg",
            "baseline_status": "passed",
            "current_status": "failed",
            "kind": "regression",
            "duration_delta_ms": 5,
        }
    ]


def test_analytics_delta_cases_missing_run(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("qasight_api.routes.analytics.get_run", lambda *, run_id: None)
    client = TestClient(create_app())
    resp = client.get("/api/v1/analytics/delta/cases?current_run_id=missing&baseline_run_id=run-1")
    assert resp.status_code == 404


def test_analytics_delta_invalid_id(monkeypatch) -> None:  # noqa: ANN001
    def _raise_invalid(self, **kwargs):  # noqa: ANN001, ARG001
        raise InvalidRunIdError("current_run_id must be a non-empty string.")

    monkeypatch.setattr(
        "qasight_api.routes.analytics.DeltaComparisonService.compare_runs", _raise_invalid
    )
    client = TestClient(create_app())
    resp = client.get("/api/v1/analytics/delta?current_run_id=&baseline_run_id=run-1")
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error"]["code"] == "invalid_input"


def test_analytics_delta_run_not_found(monkeypatch) -> None:  # noqa: ANN001
    def _raise_not_found(self, **kwargs):  # noqa: ANN001, ARG001
        raise RunNotFoundComparisonError("missing")

    monkeypatch.setattr(
        "qasight_api.routes.analytics.DeltaComparisonService.compare_runs", _raise_not_found
    )
    client = TestClient(create_app())
    resp = client.get("/api/v1/analytics/delta?current_run_id=run-2&baseline_run_id=missing")
    assert resp.status_code == 404
    payload = resp.json()
    assert payload["error"]["code"] == "not_found"


def test_analytics_delta_incompatible_data(monkeypatch) -> None:  # noqa: ANN001
    def _raise_incompatible(self, **kwargs):  # noqa: ANN001, ARG001
        raise IncompatibleRunDataError("Cannot compare runs with different test kinds.")

    monkeypatch.setattr(
        "qasight_api.routes.analytics.DeltaComparisonService.compare_runs", _raise_incompatible
    )
    client = TestClient(create_app())
    resp = client.get("/api/v1/analytics/delta?current_run_id=run-2&baseline_run_id=run-1")
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["error"]["code"] == "invalid_input"


def test_analytics_flaky_contract(monkeypatch) -> None:  # noqa: ANN001
    from qasight.services.flaky_service import FlakyReport, FlakyTest

    seen: dict[str, object] = {}

    def _fake_find(*, cycle: str, window: int) -> FlakyReport:
        seen.update(cycle=cycle, window=window)
        return FlakyReport(
            cycle=cycle,
            window=window,
            runs_analyzed=3,
            tests_analyzed=4,
            flaky=(
                FlakyTest(
                    key="k",
                    name="test_flaky",
                    group="tests.api",
                    runs=3,
                    passed=2,
                    failed=1,
                    flips=2,
                    flip_rate=1.0,
                    last_status="passed",
                    history=("passed", "failed", "passed"),
                ),
            ),
        )

    monkeypatch.setattr("qasight_api.routes.analytics.find_flaky_tests", _fake_find)
    client = TestClient(create_app())
    resp = client.get("/api/v1/analytics/flaky?cycle=fake-api")
    assert resp.status_code == 200
    assert seen == {"cycle": "fake-api", "window": 20}
    payload = resp.json()
    assert (payload["runs_analyzed"], payload["tests_analyzed"]) == (3, 4)
    assert payload["items"][0]["history"] == ["passed", "failed", "passed"]

    assert client.get("/api/v1/analytics/flaky?cycle=x&window=1").status_code == 422


def test_cycle_tests_and_test_history_contract(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from qasight.services import test_history as th

    summary = th.TestSummary(
        key="k1",
        name="test_login",
        full_name="tests.api.test_login",
        group="tests.api",
        stage="api",
        runs=4,
        passed=3,
        failed=1,
        last_status="failed",
        flaky=False,
        last_duration_ms=400,
        median_duration_ms=120,
        slowdown_ms=280,
        slowdown_pct=233.3,
    )
    point = th.TestPoint(
        run_id="run-4",
        created_at=4.0,
        status="failed",
        duration_ms=400,
        message="AssertionError",
        branch="main",
    )
    seen: dict[str, object] = {}

    def fake_list(
        cycle: str, *, window: int, query: str | None, label: str | None = None
    ) -> th.CycleTests:
        seen.update(cycle=cycle, window=window, query=query)
        return th.CycleTests(cycle=cycle, runs_considered=4, tests=(summary,))

    monkeypatch.setattr("qasight_api.routes.analytics.list_cycle_tests", fake_list)
    monkeypatch.setattr(
        "qasight_api.routes.analytics.load_test_history",
        lambda cycle, key, *, window: (
            th.TestHistory(cycle=cycle, summary=summary, points=(point,)) if key == "k1" else None
        ),
    )

    (tmp_path / "CODEOWNERS").write_text("tests/api/ @org/api\n", encoding="utf-8")
    monkeypatch.setattr(
        "qasight_api.routes.analytics.cycle_source", lambda cycle: (tmp_path, "acme/shop")
    )
    from qasight.services.test_attachments import Attachment

    screenshot = Attachment(
        name="screenshot",
        content_type="image/png",
        kind="image",
        url="history/run-4/artifacts/a-attachment.png",
        size_bytes=70,
    )
    monkeypatch.setattr("qasight_api.routes.analytics.get_run", lambda *, run_id: run_id)
    monkeypatch.setattr(
        "qasight_api.routes.analytics.attachments_for",
        lambda run, key: (screenshot,) if (run, key) == ("run-4", "k1") else (),
    )

    client = TestClient(create_app())
    listing = client.get("/api/v1/analytics/tests", params={"cycle": "smoke", "q": "login"})
    assert listing.status_code == 200
    assert seen == {"cycle": "smoke", "window": 20, "query": "login"}
    item = listing.json()["items"][0]
    assert (item["name"], item["slowdown_ms"], item["median_duration_ms"]) == (
        "test_login",
        280,
        120,
    )

    history = client.get("/api/v1/analytics/test-history", params={"cycle": "smoke", "key": "k1"})
    assert history.status_code == 200
    body = history.json()
    assert body["test"]["key"] == "k1"
    assert body["points"][0]["branch"] == "main"
    assert body["points"][0]["flip_rate"] is None
    assert body["last_failure"]["message"] == "AssertionError"
    assert body["last_failure_attachments"] == [
        {
            "name": "screenshot",
            "content_type": "image/png",
            "kind": "image",
            "url": "history/run-4/artifacts/a-attachment.png",
            "size_bytes": 70,
        }
    ]
    assert body["owners"] == ["@org/api"]
    assert body["issue_url"].startswith("https://github.com/acme/shop/issues/new?title=Flaky+test")
    missing = client.get("/api/v1/analytics/test-history", params={"cycle": "smoke", "key": "nope"})
    assert missing.status_code == 404


def test_releases_contract(monkeypatch) -> None:  # noqa: ANN001
    from qasight.services.releases import (
        ReleaseCycle,
        ReleaseDetail,
        ReleaseSummary,
        ReleaseTest,
    )

    summary = ReleaseSummary("2.4", 30.0, 40.0, 3, 2, 3, 2, 1, 0)
    previous = ReleaseSummary("2.3", 10.0, 10.0, 1, 1, 2, 1, 1, 0)
    detail = ReleaseDetail(
        summary=summary,
        cycles=(ReleaseCycle("api", "a4", 40.0, 2, 2, 1, 1, 0, 0),),
        failing=(ReleaseTest("api", "cart", "cart", "failed", "nope", "a4", new=True),),
        fixed=(),
        flaky=0,
        previous=previous,
    )
    monkeypatch.setattr("qasight_api.routes.analytics.list_releases", lambda: (summary, previous))
    monkeypatch.setattr(
        "qasight_api.routes.analytics.load_release", lambda r: detail if r == "2.4" else None
    )
    client = TestClient(create_app())
    listed = client.get("/api/v1/releases").json()
    assert [i["release"] for i in listed["items"]] == ["2.4", "2.3"]
    assert listed["items"][0]["pass_rate"] == 66.7
    body = client.get("/api/v1/releases/2.4").json()
    assert body["previous"]["release"] == "2.3"
    assert body["failing"][0] == {
        "cycle": "api",
        "key": "cart",
        "name": "cart",
        "status": "failed",
        "message": "nope",
        "run_id": "a4",
        "new": True,
    }
    assert client.get("/api/v1/releases/9.9").status_code == 404
