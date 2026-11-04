"""Dispatcher for ``qasight report`` — picks generate / serve / export."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

from qasight.engine.exit_codes import EngineExitCode
from qasight.reporting.collector import collect_results
from qasight.reporting.exporter import write_json_summary, write_junit_xml
from qasight.reporting.paths import relpath_for_display


def dispatch_report(
    *,
    console: Console,
    artifacts_root: Path,
    plan_name: str | None,
    generate_only: bool,
    port: int,
    host: str,
    out_dir: Path,
    fmt: str,
    summary_out: Path | None,
    inject_history: bool = True,
) -> int:
    """Wire one ``qasight report`` invocation to the right backend."""
    results = collect_results(artifacts_root, plan_name=plan_name)
    if not results.stages:
        console.print(
            f"[fail]no results found under {artifacts_root}[/] "
            "— run `qasight run --cycle <name>` (or `qasight run` with a single cycle) first."
        )
        return int(EngineExitCode.INVALID_INPUT)

    console.print(
        "[bold magenta]────────────────────────── Post-Workout Review ──────────────────────────[/]"
    )

    fmt_normalised = fmt.lower().strip()
    if fmt_normalised in {"json", "junit"}:
        target = summary_out or (
            out_dir.parent
            / f"summary.{fmt_normalised}.{'json' if fmt_normalised == 'json' else 'xml'}"
        )
        try:
            if fmt_normalised == "json":
                written = write_json_summary(results=results, out=target)
            else:
                written = write_junit_xml(results=results, out=target)
        except OSError as exc:
            console.print(f"[fail]failed to write {target}: {exc}[/]")
            return int(EngineExitCode.INFRA_FAILURE)
        rel = relpath_for_display(Path(written))
        console.print(
            f"[ok]wrote {fmt_normalised} summary to[/] [link=file://{Path(written).resolve().as_uri()}]{rel}[/]"
        )
        return int(EngineExitCode.SUCCESS)

    if fmt_normalised != "html":
        console.print(f"[fail]unknown --format {fmt_normalised!r}[/]")
        return int(EngineExitCode.INVALID_INPUT)

    from qasight.reporting.history_inject import try_inject_prior_history

    if inject_history:
        try_inject_prior_history(
            artifacts_root=artifacts_root,
            plan_name=plan_name,
            console=console,
            enabled=True,
        )

    from qasight.reporting.allure import (
        AllureCLINotFoundError,
        generate_html,
    )
    from qasight.reporting.server import open_generated_report, resolve_serve_port

    try:
        outcome = generate_html(result_dirs=results.result_dirs, out_dir=out_dir)
    except AllureCLINotFoundError as exc:
        console.print(f"[fail]{exc}[/]")
        return int(EngineExitCode.INFRA_FAILURE)

    if not outcome.ok:
        console.print(f"[fail]allure generate failed:[/] {outcome.message}")
        return int(EngineExitCode.INFRA_FAILURE)

    out_resolved = outcome.out_dir.resolve()
    index_path = out_resolved / "index.html"
    rel_idx = relpath_for_display(index_path)
    idx_uri = index_path.resolve().as_uri()

    console.print("[ok]Workout summary compiled. Open the dashboard to see your gains.[/]")
    console.print(f"[muted]index.html[/] [link={idx_uri}]{rel_idx}[/]")

    if generate_only:
        console.print(
            "[dim]`--generate-only`: static files only. Run `qasight report` without that flag "
            "for a local HTTP server (avoids browser file:// restrictions).[/]"
        )
        return int(EngineExitCode.SUCCESS)

    if shutil.which("allure") is None:
        console.print(
            "[fail]Opening the report needs the Allure CLI (`allure`). "
            "Install it from https://allurereport.org/docs/install/, or run "
            "`qasight report --generate-only` and open index.html yourself.[/]"
        )
        return int(EngineExitCode.INFRA_FAILURE)

    serve_port = resolve_serve_port(host, port)
    if serve_port != port:
        console.print(
            f"[muted]Port {port} is in use; serving the dashboard on[/] [bold]{serve_port}[/] instead."
        )
    url = f"http://{host}:{serve_port}/"
    console.print(f"[bold]Dashboard[/] [link={url}]{url}[/]")
    console.print("[dim]Press Ctrl+C here to stop the server when you are done.[/]")

    code = open_generated_report(report_dir=out_resolved, host=host, port=serve_port)
    if code == 127:
        console.print("[fail]``allure`` CLI was not found on PATH.[/]")
        return int(EngineExitCode.INFRA_FAILURE)
    if code not in (0, 130):
        return int(EngineExitCode.INFRA_FAILURE)
    return int(code)
