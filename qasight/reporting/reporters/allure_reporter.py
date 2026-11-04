"""Allure HTML generation and optional local dashboard serve."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from qasight.reporting.collector import CollectedResults
from qasight.reporting.reporters.base import BaseReporter, ReportContext, ReporterResult

if TYPE_CHECKING:
    from rich.console import Console


class AllureReporter(BaseReporter):
    @property
    def reporter_type(self) -> str:
        return "allure"

    def publish(
        self,
        *,
        results: CollectedResults,
        context: ReportContext,
        console: Console | None = None,
    ) -> ReporterResult:
        if not results.stages:
            return ReporterResult(ok=False, message="no Allure result directories found.")

        if context.inject_history:
            from qasight.reporting.history_inject import try_inject_prior_history

            try_inject_prior_history(
                artifacts_root=context.artifacts_root,
                plan_name=context.plan_name,
                console=console,
                enabled=True,
                trend_depth=context.trend_depth,
            )

        if context.out_dir is None and context.run_report_root is not None:
            return self._publish_per_framework(results=results, context=context, console=console)

        out_dir = (
            (
                context.out_dir
                or Path(self._options.get("out_dir", str(context.artifacts_root / "allure-report")))
            )
            .expanduser()
            .resolve()
        )

        from qasight.reporting.allure import AllureCLINotFoundError, generate_html

        try:
            outcome = generate_html(result_dirs=results.result_dirs, out_dir=out_dir)
        except AllureCLINotFoundError as exc:
            return ReporterResult(ok=False, message=str(exc))

        if not outcome.ok:
            return ReporterResult(ok=False, message=outcome.message)

        index_path = outcome.out_dir.resolve() / "index.html"
        if context.generate_only or context.ci or not context.open_browser:
            return ReporterResult(
                ok=True,
                message=f"Allure HTML at {index_path}",
                artifacts=(index_path,),
            )

        from qasight.reporting.allure_cli import is_allure_available

        if not is_allure_available():
            return ReporterResult(
                ok=True,
                message=f"Allure HTML at {index_path} (Allure 3 CLI missing; cannot serve dashboard)",
                artifacts=(index_path,),
            )

        from qasight.reporting.server import open_generated_report, resolve_serve_port

        serve_port = resolve_serve_port(context.host, context.port)
        if console is not None and serve_port != context.port:
            console.print(
                f"[muted]Port {context.port} is in use; serving the dashboard on[/] [bold]{serve_port}[/] instead."
            )
        code = open_generated_report(
            report_dir=outcome.out_dir.resolve(), host=context.host, port=serve_port
        )
        if code == 127:
            return ReporterResult(
                ok=True,
                message=f"Allure HTML at {index_path} (allure serve not found)",
                artifacts=(index_path,),
            )
        if code not in (0, 130):
            return ReporterResult(ok=False, message=f"allure serve exited {code}")

        return ReporterResult(
            ok=True,
            message=f"Allure dashboard served from {index_path}",
            artifacts=(index_path,),
        )

    def _publish_per_framework(
        self,
        *,
        results: CollectedResults,
        context: ReportContext,
        console: Console | None = None,
    ) -> ReporterResult:
        """Generate one Allure report per framework under run_report_root/allure_reports/<framework>/."""
        from qasight.reporting.allure import AllureCLINotFoundError, generate_html

        base_dir = context.run_report_root / "allure_reports"  # type: ignore[operator]
        artifacts: list[Path] = []
        messages: list[str] = []
        any_ok = False
        for framework, result_dirs in results.by_framework.items():
            out_dir = (base_dir / framework).expanduser().resolve()
            try:
                outcome = generate_html(result_dirs=result_dirs, out_dir=out_dir)
            except AllureCLINotFoundError as exc:
                return ReporterResult(ok=False, message=str(exc))
            if not outcome.ok:
                messages.append(f"{framework}: {outcome.message}")
                continue
            any_ok = True
            index_path = outcome.out_dir.resolve() / "index.html"
            artifacts.append(index_path)
            messages.append(f"{framework}: {index_path}")

        return ReporterResult(
            ok=any_ok,
            message="Allure HTML — " + "; ".join(messages),
            artifacts=tuple(artifacts),
        )
