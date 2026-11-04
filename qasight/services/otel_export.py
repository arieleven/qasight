"""A recorded run as an OpenTelemetry trace, sent to any OTLP endpoint.

One trace per run: a root span for the run, a child span per stage and a child
span per test under its stage, with the real start and stop times from the
results. Datadog, Grafana Tempo, Honeycomb, Jaeger and anything else that takes
OTLP then show test runs next to the services they test.

The trace is built as plain data first (:func:`build_trace`), so ``qasight otel
--dry-run`` and the tests need no OpenTelemetry install. Sending it
(:func:`send`) needs the optional extra: ``pip install 'qasight[otel]'``.

Nothing here runs unless asked: the ``otel`` reporter or ``qasight otel``.
Where to send comes from the standard ``OTEL_EXPORTER_OTLP_*`` variables, so a
collector already set up for the services works unchanged. Headers often hold
API keys, so they come from the environment or a ``${env:...}`` reporter
option, never from a command-line flag, and are never printed.

See docs/Specs & ADRs/OpenTelemetry Export - 2026-10-30.md.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qasight import paths
from qasight.history.read_model import get_run, list_cycle_runs, list_recent_runs
from qasight.history.views import CompletedRunView
from qasight.services.report_archive_diff import _case_stage, _label_value

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExporter

AttrValue = str | int | float | bool
INSTALL_HINT = "pip install 'qasight[otel]'"
PROTOCOLS = ("http/protobuf", "grpc")
DEFAULT_SERVICE_NAME = "qasight"
_FAILED = frozenset({"failed", "broken"})
_MESSAGE_LIMIT = 500
_BATCH = 512


class OtelExportError(RuntimeError):
    """The trace could not be sent: the collector refused it or could not be reached."""


class OtelSetupError(OtelExportError):
    """Nothing was sent: the extra is not installed or an option is wrong."""


@dataclass(frozen=True)
class SpanData:
    """One span, before it meets the SDK. ``key`` / ``parent`` link spans within the trace."""

    key: str
    parent: str | None
    name: str
    start_ns: int
    end_ns: int
    attributes: dict[str, AttrValue] = field(default_factory=dict)
    error: bool = False
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "key": self.key,
            "parent": self.parent,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "status": "error" if self.error else "unset",
            **({"message": self.message} if self.message else {}),
            "attributes": self.attributes,
        }


@dataclass(frozen=True)
class RunTrace:
    run_id: str
    spans: tuple[SpanData, ...]  # parents always come before their children

    def to_json(self) -> str:
        return json.dumps(
            {"run_id": self.run_id, "spans": [s.to_dict() for s in self.spans]},
            indent=2,
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class _Case:
    name: str
    full_name: str
    status: str
    stage: str
    framework: str
    start_ms: int | None
    stop_ms: int | None
    message: str | None


def _ns(seconds: float) -> int:
    return int(seconds * 1_000_000_000)


def _load_cases(plan_root: Path) -> list[_Case]:
    """Every ``*-result.json`` under a run's snapshot, with its start and stop times."""
    cases: list[_Case] = []
    if not plan_root.is_dir():
        return cases
    for path in sorted(plan_root.rglob("*-result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        start, stop = data.get("start"), data.get("stop")
        timed = isinstance(start, int) and isinstance(stop, int) and stop >= start
        details = data.get("statusDetails")
        message = details.get("message") if isinstance(details, dict) else None
        cases.append(
            _Case(
                name=str(data.get("name") or data.get("fullName") or path.stem),
                full_name=str(data.get("fullName") or data.get("name") or ""),
                status=str(data.get("status") or "unknown").lower(),
                stage=_case_stage(path, plan_root),
                framework=_label_value(data.get("labels"), "framework"),
                start_ms=start if timed else None,
                stop_ms=stop if timed else None,
                message=str(message)[:_MESSAGE_LIMIT] if message else None,
            )
        )
    return cases


def _run_attributes(run: CompletedRunView) -> dict[str, AttrValue]:
    attrs: dict[str, AttrValue] = {
        "testo.run_id": run.run_id,
        "testo.exit_code": run.returncode,
        "test.suite.run.status": "success" if run.returncode == 0 else "failure",
    }
    optional: dict[str, AttrValue | None] = {
        "testo.cycle": run.cycle,
        "test.suite.name": run.cycle,
        "testo.tests.total": run.total_tests,
        "testo.tests.passed": run.passed,
        "testo.tests.failed": run.failed,
        "testo.tests.broken": run.broken,
        "testo.tests.skipped": run.skipped,
        "testo.repository": run.repository,
        "vcs.ref.head.name": run.branch,
        "vcs.ref.head.revision": run.commit,
        "vcs.ref.base.name": run.base_branch,
    }
    attrs.update({k: v for k, v in optional.items() if v is not None})
    return attrs


def _case_name(case: _Case) -> str:
    """The test's full name, with its parameters when the name has them and the full name doesn't.

    pytest gives every case of a parametrized test the same full name
    (``tests.test_math#test_add``) and the parameters only in the name
    (``test_add[1-2]``); the span name alone can't tell the cases apart.
    """
    full, name = case.full_name, case.name
    if not full:
        return name
    if not name or name in full:
        return full
    leaf = re.split(r"[#.:/ ]", full)[-1]
    if leaf and name.startswith(leaf) and len(name) > len(leaf):
        return full + name[len(leaf) :]
    return full


def _test_span(case: _Case, index: int, parent: str, fallback_ns: int) -> SpanData:
    if case.start_ms is not None and case.stop_ms is not None:
        start, end = case.start_ms * 1_000_000, case.stop_ms * 1_000_000
    else:
        start = end = fallback_ns
    attrs: dict[str, AttrValue] = {
        "test.case.name": _case_name(case),
        "testo.test.status": case.status,
    }
    if case.status == "passed":
        attrs["test.case.result.status"] = "pass"
    elif case.status in _FAILED:
        attrs["test.case.result.status"] = "fail"
    if case.stage:
        attrs["testo.stage"] = case.stage
    if case.framework:
        attrs["testo.framework"] = case.framework
    failed = case.status in _FAILED
    return SpanData(
        key=f"test:{index}",
        parent=parent,
        name=case.name,
        start_ns=start,
        end_ns=end,
        attributes=attrs,
        error=failed,
        message=case.message if failed else None,
    )


def build_trace(run: CompletedRunView, cases: list[_Case] | None = None) -> RunTrace:
    """The run, its stages and its tests as spans (``cases`` default: the run's snapshot)."""
    if cases is None:
        cases = _load_cases(paths.ORCHESTRATOR_ROOT / run.snapshot_dir) if run.snapshot_dir else []

    run_start = _ns(run.started_at) if run.started_at else 0
    by_stage: dict[str, list[_Case]] = {}
    for case in cases:
        by_stage.setdefault(case.stage, []).append(case)

    stage_info = {str(s.get("name")): s for s in run.stage_health if s.get("name")}
    stage_names = list(stage_info) + [n for n in by_stage if n and n not in stage_info]

    children: list[SpanData] = []
    # Stages without timed tests are laid out one after another from the run's start.
    cursor = run_start
    for stage in stage_names:
        info = stage_info.get(stage, {})
        timed = [c for c in by_stage.get(stage, []) if c.start_ms is not None]
        duration_s = info.get("duration_s")
        if timed:
            start = min(c.start_ms or 0 for c in timed) * 1_000_000
            end = max(c.stop_ms or 0 for c in timed) * 1_000_000
        else:
            start = cursor
            end = start + (_ns(float(duration_s)) if isinstance(duration_s, int | float) else 0)
        cursor = max(cursor, end)
        returncode = info.get("returncode")
        attrs: dict[str, AttrValue] = {"testo.stage": stage}
        for key, attr in (("framework", "testo.framework"), ("tier", "testo.tier")):
            if info.get(key):
                attrs[attr] = str(info[key])
        if isinstance(returncode, int):
            attrs["testo.exit_code"] = returncode
        key = f"stage:{stage}"
        children.append(
            SpanData(
                key=key,
                parent="run",
                name=f"stage {stage}",
                start_ns=start,
                end_ns=end,
                attributes=attrs,
                error=isinstance(returncode, int) and returncode != 0,
                message=f"exit code {returncode}" if returncode else None,
            )
        )
        for case in by_stage.get(stage, []):
            children.append(_test_span(case, len(children), key, start))
    # Results that belong to no stage (some imports) hang off the run itself.
    for case in by_stage.get("", []):
        children.append(_test_span(case, len(children), "run", run_start))

    starts = [s.start_ns for s in children if s.start_ns] + ([run_start] if run_start else [])
    ends = [s.end_ns for s in children] + ([_ns(run.finished_at)] if run.finished_at else [])
    root_start = min(starts) if starts else 0
    root_end = max([root_start, *ends])
    root = SpanData(
        key="run",
        parent=None,
        name=f"qasight {run.cycle}" if run.cycle else "qasight run",
        start_ns=root_start,
        end_ns=root_end,
        attributes=_run_attributes(run),
        error=run.returncode != 0,
        message=f"exit code {run.returncode}" if run.returncode else None,
    )
    return RunTrace(run_id=run.run_id, spans=(root, *children))


def load_trace(*, cycle: str | None = None, run_id: str | None = None) -> RunTrace | None:
    """The trace for ``run_id``, else ``cycle``'s latest run, else the latest run at all."""
    if run_id:
        run = get_run(run_id=run_id)
    else:
        recent = list_cycle_runs(cycle, limit=1) if cycle else list_recent_runs(limit=1)
        run = recent[0] if recent else None
    return build_trace(run) if run is not None else None


def parse_headers(raw: str | None) -> dict[str, str]:
    """``key=value,key2=value2`` (the ``OTEL_EXPORTER_OTLP_HEADERS`` format) as a dict."""
    out: dict[str, str] = {}
    for item in (raw or "").split(","):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def resolve_protocol(protocol: str | None, env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = (
        protocol
        or env.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")
        or env.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "http/protobuf"
    )
    value = value.strip().lower()
    if value not in PROTOCOLS:
        raise OtelSetupError(f"protocol must be one of {', '.join(PROTOCOLS)}, not {value!r}")
    return value


def _exporter(
    protocol: str, endpoint: str | None, headers: dict[str, str] | None, timeout_s: float
) -> SpanExporter:
    """An OTLP span exporter. Unset ``endpoint`` / ``headers`` fall back to the OTEL_* env."""
    if protocol == "grpc":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter as GrpcExporter,
            )
        except ImportError as exc:
            raise OtelSetupError(
                "protocol grpc needs: pip install opentelemetry-exporter-otlp-proto-grpc"
            ) from exc
        return GrpcExporter(endpoint=endpoint, headers=headers or None, timeout=int(timeout_s))
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HttpExporter,
        )
    except ImportError as exc:
        raise OtelSetupError(f"OpenTelemetry export needs: {INSTALL_HINT}") from exc
    given = (
        endpoint
        or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    )
    if given and "://" not in given:
        # The HTTP exporter would fail later with "No connection adapters" (infra, exit 3).
        raise OtelSetupError(
            f"the OTLP endpoint must be a URL like http://{given.strip('/')}, not {given!r}"
        )
    if endpoint and not endpoint.rstrip("/").endswith("/v1/traces"):
        # A base URL (like OTEL_EXPORTER_OTLP_ENDPOINT) gets the traces path appended.
        endpoint = endpoint.rstrip("/") + "/v1/traces"
    return HttpExporter(endpoint=endpoint, headers=headers or None, timeout=timeout_s)


def send(
    trace: RunTrace,
    *,
    endpoint: str | None = None,
    headers: dict[str, str] | None = None,
    protocol: str | None = None,
    service_name: str | None = None,
    timeout_s: float = 10.0,
    exporter: SpanExporter | None = None,
) -> str:
    """Send ``trace`` over OTLP and return its trace id (hex). Raises :class:`OtelExportError`."""
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.context import Context
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    except ImportError as exc:
        raise OtelSetupError(f"OpenTelemetry export needs: {INSTALL_HINT}") from exc

    if exporter is None:
        exporter = _exporter(resolve_protocol(protocol), endpoint, headers, timeout_s)

    from qasight import __version__

    name = service_name or os.environ.get("OTEL_SERVICE_NAME") or DEFAULT_SERVICE_NAME
    provider = TracerProvider(resource=Resource.create({"service.name": name}))
    # Spans are collected in memory first and exported in batches below, so a
    # collector that refuses them is an error here instead of a log line.
    collected = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(collected))
    tracer = provider.get_tracer("qasight.otel", __version__)

    live: dict[str, otel_trace.Span] = {}
    ends: dict[str, int] = {}
    for data in trace.spans:
        parent = live.get(data.parent) if data.parent else None
        context = otel_trace.set_span_in_context(parent) if parent is not None else Context()
        span = tracer.start_span(
            data.name, context=context, attributes=data.attributes, start_time=data.start_ns
        )
        if data.error:
            span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, data.message))
        live[data.key] = span
        ends[data.key] = data.end_ns
    for key in reversed(list(live)):
        live[key].end(end_time=ends[key])

    finished = collected.get_finished_spans()
    try:
        for i in range(0, len(finished), _BATCH):
            if exporter.export(finished[i : i + _BATCH]) is not SpanExportResult.SUCCESS:
                raise OtelExportError("the OTLP endpoint did not accept the spans")
    finally:
        exporter.shutdown()
        provider.shutdown()
    root = live["run"].get_span_context()
    return format(root.trace_id, "032x")
