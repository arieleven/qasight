"""``qasight otel`` — send a recorded run to an OpenTelemetry collector as a trace."""

from __future__ import annotations

import typer

from qasight.engine.exit_codes import EngineExitCode


def otel(
    cycle: str | None = typer.Option(None, "--cycle", help="Latest run of this cycle."),
    run_id: str | None = typer.Option(None, "--run-id", help="A specific run (run-history id)."),
    endpoint: str | None = typer.Option(
        None,
        "--endpoint",
        help="OTLP endpoint (default: $OTEL_EXPORTER_OTLP_ENDPOINT, else localhost). "
        "Headers come only from $OTEL_EXPORTER_OTLP_HEADERS.",
    ),
    protocol: str | None = typer.Option(
        None, "--protocol", help="http/protobuf (default) or grpc (extra package)."
    ),
    service_name: str | None = typer.Option(
        None, "--service-name", help="service.name (default: $OTEL_SERVICE_NAME or qasight)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the spans as JSON instead of sending them."
    ),
) -> None:
    """Send the run, its stages and its tests as one OpenTelemetry trace over OTLP."""
    from qasight.services import otel_export

    trace = otel_export.load_trace(cycle=cycle.strip() if cycle else None, run_id=run_id)
    if trace is None:
        typer.echo("no recorded run found", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    if dry_run:
        typer.echo(trace.to_json())
        raise typer.Exit(code=0)

    try:
        trace_id = otel_export.send(
            trace, endpoint=endpoint, protocol=protocol, service_name=service_name
        )
    except otel_export.OtelSetupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from None
    except otel_export.OtelExportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE)) from None
    typer.echo(f"sent {len(trace.spans)} spans for run {trace.run_id} (trace {trace_id})", err=True)
