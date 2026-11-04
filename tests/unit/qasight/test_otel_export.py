"""Runs as OpenTelemetry traces (qasight/services/otel_export.py, ``otel`` reporter, ``qasight otel``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.history.views import CompletedRunView
from qasight.reporting.collector import CollectedResults
from qasight.reporting.reporters.base import ReportContext
from qasight.reporting.reporters.factory import ReporterFactory
from qasight.reporting.reporters.otel_reporter import OtelReporter
from qasight.repository.models import RunStatus
from qasight.services import otel_export

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace.export import SpanExportResult  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode  # noqa: E402

from tests._history_fakes import cycle_runs_from

T0 = 1_760_000_000.0  # run start, seconds
MS = int(T0 * 1000)


def _run(*, returncode: int = 1, snapshot: str | None = "snap") -> CompletedRunView:
    return CompletedRunView(
        run_id="run-1",
        status=RunStatus.COMPLETED,
        created_at=T0,
        started_at=T0,
        finished_at=T0 + 10,
        test_kind="cycle",
        returncode=returncode,
        wall_duration_ms=10_000.0,
        metrics_duration_ms=None,
        total_tests=3,
        passed=1,
        failed=1,
        broken=0,
        skipped=1,
        avg_case_ms=None,
        health_pct=None,
        target_repo=None,
        snapshot_dir=snapshot,
        audit_json=None,
        cycle="api",
        stage_health=[
            {"name": "unit", "framework": "pytest", "tier": "unit", "returncode": 1},
            {"name": "lint", "framework": "command", "returncode": 0, "duration_s": 2.5},
        ],
        branch="feature/x",
        commit="abc123",
        base_branch="main",
        repository="acme/shop",
    )


def _result(root: Path, stage: str, name: str, status: str, start: int, stop: int, **extra: Any):
    folder = root / stage / "allure-results" / "pytest"
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "fullName": f"tests.test_mod#{name}",
        "status": status,
        "start": start,
        "stop": stop,
        "labels": [{"name": "framework", "value": "pytest"}],
        **extra,
    }
    (folder / f"{name}-result.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snap"
    _result(root, "unit", "test_ok", "passed", MS + 1000, MS + 1500)
    _result(
        root,
        "unit",
        "test_bad",
        "failed",
        MS + 1500,
        MS + 4000,
        statusDetails={"message": "AssertionError: 1 != 2"},
    )
    _result(root, "unit", "test_skip", "skipped", MS + 4000, MS + 4000)
    monkeypatch.setattr(otel_export.paths, "ORCHESTRATOR_ROOT", tmp_path)
    return root


def _by_name(trace: otel_export.RunTrace) -> dict[str, otel_export.SpanData]:
    return {s.name: s for s in trace.spans}


def test_trace_has_run_stage_and_test_spans_with_real_times(snapshot: Path) -> None:
    trace = otel_export.build_trace(_run())
    spans = _by_name(trace)

    assert [s.name for s in trace.spans][:2] == ["qasight api", "stage unit"]
    root = spans["qasight api"]
    assert root.parent is None and root.error
    assert root.attributes["testo.run_id"] == "run-1"
    assert root.attributes["test.suite.run.status"] == "failure"
    assert root.attributes["vcs.ref.head.name"] == "feature/x"
    assert root.attributes["vcs.ref.head.revision"] == "abc123"
    assert root.start_ns == int(T0 * 1e9)
    assert root.end_ns == int((T0 + 10) * 1e9)

    unit = spans["stage unit"]
    assert unit.parent == "run" and unit.error
    assert unit.start_ns == (MS + 1000) * 1_000_000
    assert unit.end_ns == (MS + 4000) * 1_000_000

    bad = spans["test_bad"]
    assert bad.parent == unit.key and bad.error
    assert bad.message == "AssertionError: 1 != 2"
    assert bad.attributes["test.case.result.status"] == "fail"
    assert bad.attributes["test.case.name"] == "tests.test_mod#test_bad"
    assert bad.end_ns - bad.start_ns == 2_500_000_000

    assert spans["test_ok"].attributes["test.case.result.status"] == "pass"
    assert not spans["test_ok"].error
    assert "test.case.result.status" not in spans["test_skip"].attributes


def test_stage_without_tests_uses_its_recorded_duration(snapshot: Path) -> None:
    lint = _by_name(otel_export.build_trace(_run()))["stage lint"]
    assert not lint.error
    assert lint.end_ns - lint.start_ns == 2_500_000_000


def test_run_without_snapshot_still_has_run_and_stage_spans() -> None:
    trace = otel_export.build_trace(_run(returncode=0, snapshot=None))
    assert [s.name for s in trace.spans] == ["qasight api", "stage unit", "stage lint"]
    assert not trace.spans[0].error


def test_send_exports_one_trace_with_parents_and_statuses(snapshot: Path) -> None:
    exporter = InMemorySpanExporter()
    trace_id = otel_export.send(
        otel_export.build_trace(_run()), exporter=exporter, service_name="shop-tests"
    )

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert len(spans) == 6
    assert {format(s.context.trace_id, "032x") for s in spans.values()} == {trace_id}
    root, unit, bad = spans["qasight api"], spans["stage unit"], spans["test_bad"]
    assert root.parent is None
    assert unit.parent is not None and unit.parent.span_id == root.context.span_id
    assert bad.parent is not None and bad.parent.span_id == unit.context.span_id
    assert bad.status.status_code is StatusCode.ERROR
    assert bad.status.description == "AssertionError: 1 != 2"
    assert bad.start_time == (MS + 1500) * 1_000_000
    assert root.resource.attributes["service.name"] == "shop-tests"


def test_send_raises_when_the_collector_refuses(snapshot: Path) -> None:
    class Refusing(InMemorySpanExporter):
        def export(self, spans):  # type: ignore[no-untyped-def]
            return SpanExportResult.FAILURE

    with pytest.raises(otel_export.OtelExportError) as err:
        otel_export.send(otel_export.build_trace(_run()), exporter=Refusing())
    assert not isinstance(err.value, otel_export.OtelSetupError)


def test_headers_and_protocol_parsing() -> None:
    assert otel_export.parse_headers("api-key=abc, x-tenant = t1,broken") == {
        "api-key": "abc",
        "x-tenant": "t1",
    }
    assert otel_export.resolve_protocol(None, {}) == "http/protobuf"
    assert otel_export.resolve_protocol(None, {"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}) == "grpc"
    with pytest.raises(otel_export.OtelSetupError):
        otel_export.resolve_protocol("http/json", {})


def test_http_exporter_appends_the_traces_path() -> None:
    exporter = otel_export._exporter("http/protobuf", "http://collector:4318", None, 5)
    assert exporter._endpoint == "http://collector:4318/v1/traces"  # type: ignore[attr-defined]


def test_otel_reporter_is_registered_and_sends(
    snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(otel_export, "get_run", lambda *, run_id: _run())
    monkeypatch.setattr(otel_export, "send", lambda trace, **kw: sent.append(kw) or "f" * 32)

    reporter = ReporterFactory.build(config_reporters=(), overrides=["otel"])[0]
    assert isinstance(reporter, OtelReporter)
    reporter = OtelReporter(options={"headers": "api-key=s3cret", "service_name": "shop"})
    ctx = ReportContext(artifacts_root=snapshot, run_id="run-1")
    outcome = reporter.publish(results=CollectedResults(artifacts_root=snapshot), context=ctx)

    assert outcome.ok and "6 spans" in outcome.message
    assert "s3cret" not in outcome.message
    assert sent[0]["headers"] == {"api-key": "s3cret"}
    assert sent[0]["service_name"] == "shop"


def test_otel_reporter_needs_a_recorded_run(tmp_path: Path) -> None:
    outcome = OtelReporter().publish(
        results=CollectedResults(artifacts_root=tmp_path),
        context=ReportContext(artifacts_root=tmp_path),
    )
    assert not outcome.ok and "recorded run" in outcome.message


def test_otel_cli_dry_run_and_errors(snapshot: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otel_export, "list_cycle_runs", cycle_runs_from([_run()]))

    dry = CliRunner().invoke(app, ["otel", "--cycle", "api", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    body = json.loads(dry.stdout)
    assert body["run_id"] == "run-1" and len(body["spans"]) == 6

    missing = CliRunner().invoke(app, ["otel", "--cycle", "nope"])
    assert missing.exit_code == 2

    bad_protocol = CliRunner().invoke(app, ["otel", "--cycle", "api", "--protocol", "smoke"])
    assert bad_protocol.exit_code == 2

    def refuse(trace: Any, **kw: Any) -> str:
        raise otel_export.OtelExportError("the OTLP endpoint did not accept the spans")

    monkeypatch.setattr(otel_export, "send", refuse)
    refused = CliRunner().invoke(app, ["otel", "--cycle", "api"])
    assert refused.exit_code == 3


def _case(name: str, full_name: str) -> Any:
    return otel_export._Case(
        name=name,
        full_name=full_name,
        status="passed",
        stage="unit",
        framework="pytest",
        start_ms=None,
        stop_ms=None,
        message=None,
    )


@pytest.mark.parametrize(
    ("name", "full_name", "expected"),
    [
        # pytest: every parametrized case shares the full name; the name has the params.
        ("test_add[1-2]", "tests.test_math#test_add", "tests.test_math#test_add[1-2]"),
        ("test_add", "tests.test_math#test_add", "tests.test_math#test_add"),
        ("ok", "s.ok", "s.ok"),
        ("A nicer title", "s.test_x", "s.test_x"),  # a display title: keep the stable full name
        ("only_name", "", "only_name"),
    ],
)
def test_test_case_name_keeps_parameters(name: str, full_name: str, expected: str) -> None:
    assert otel_export._case_name(_case(name, full_name)) == expected


def test_endpoint_without_scheme_is_bad_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    with pytest.raises(otel_export.OtelSetupError, match="http://collector:4318"):
        otel_export._exporter("http/protobuf", "collector:4318", None, 5)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "collector:4318")
    with pytest.raises(otel_export.OtelSetupError):
        otel_export._exporter("http/protobuf", None, None, 5)


def test_cli_endpoint_without_scheme_exits_2(
    snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(otel_export, "list_cycle_runs", cycle_runs_from([_run()]))
    result = CliRunner().invoke(app, ["otel", "--cycle", "api", "--endpoint", "collector:4318"])
    assert result.exit_code == 2
    assert "must be a URL" in result.output
