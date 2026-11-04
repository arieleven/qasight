"""``otel`` reporter: the run as an OpenTelemetry trace, sent over OTLP after ``qasight run``.

Options (all optional; the standard ``OTEL_EXPORTER_OTLP_*`` variables are used
for anything left out)::

    reporters:
      - type: otel
        endpoint: https://otlp.example:4318      # default: $OTEL_EXPORTER_OTLP_ENDPOINT
        headers: ${env:OTLP_HEADERS}             # "key=value,..."; keep keys in secrets
        protocol: http/protobuf                  # or grpc (extra package)
        service_name: checkout-tests             # default: $OTEL_SERVICE_NAME or qasight

Needs ``pip install 'qasight[otel]'`` and a recorded run (not ``--no-persist``).
The trace comes from :mod:`qasight.services.otel_export`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qasight.reporting.collector import CollectedResults
from qasight.reporting.reporters.base import BaseReporter, ReportContext, ReporterResult

if TYPE_CHECKING:
    from rich.console import Console


class OtelReporter(BaseReporter):
    @property
    def reporter_type(self) -> str:
        return "otel"

    def _option(self, key: str) -> str | None:
        return (self._options.get(key) or "").strip() or None

    def publish(
        self,
        *,
        results: CollectedResults,
        context: ReportContext,
        console: Console | None = None,
    ) -> ReporterResult:
        from qasight.services import otel_export

        if not context.run_id:
            return ReporterResult(
                ok=False, message="OpenTelemetry: needs a recorded run (persist)."
            )
        trace = otel_export.load_trace(run_id=context.run_id)
        if trace is None:
            return ReporterResult(
                ok=False, message=f"OpenTelemetry: run {context.run_id} not found."
            )
        try:
            trace_id = otel_export.send(
                trace,
                endpoint=self._option("endpoint"),
                headers=otel_export.parse_headers(self._option("headers")) or None,
                protocol=self._option("protocol"),
                service_name=self._option("service_name"),
            )
        except otel_export.OtelExportError as exc:
            return ReporterResult(ok=False, message=f"OpenTelemetry: {exc}")
        return ReporterResult(
            ok=True, message=f"OpenTelemetry: sent {len(trace.spans)} spans (trace {trace_id})."
        )
