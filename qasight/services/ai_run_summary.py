"""One failed run's AI summary for the CLI and the pull request comment.

The same :class:`~qasight.services.failure_analysis_service.FailureAnalysisService`
the API uses, with the settings from :func:`~qasight.services.ai.cli_settings.settings_store`,
so a summary made here is stored with the run and the UI shows it too.
"""

from __future__ import annotations

from dataclasses import dataclass

_REASONS = {
    "ai_feature_disabled": "AI summaries are off. Turn them on with: qasight ai setup",
    "provider_misconfigured": "The AI provider isn't set up",
    "provider_timeout": "The AI provider didn't answer in time",
}


@dataclass(frozen=True)
class RunAiSummary:
    run_id: str | None
    text: str | None
    reason: str = ""


def _pick_run(cycle: str | None, run_id: str | None) -> str | None:
    from qasight.history.read_model import list_cycle_runs, list_recent_runs

    if run_id:
        return run_id
    runs = list_cycle_runs(cycle) if cycle else list_recent_runs(limit=50)
    failed = next((r for r in runs if r.returncode != 0), None)
    return failed.run_id if failed else None


def summarize(
    *, cycle: str | None = None, run_id: str | None = None, refresh: bool = False
) -> RunAiSummary:
    from qasight.services.ai.cli_settings import settings_store
    from qasight.services.failure_analysis_service import FailureAnalysisService

    picked = _pick_run(cycle, run_id)
    if picked is None:
        where = f" of {cycle}" if cycle else ""
        return RunAiSummary(None, None, f"No failed run{where} to summarize.")
    try:
        store = settings_store()
    except ValueError as exc:
        return RunAiSummary(picked, None, str(exc))
    result = FailureAnalysisService(settings_store=store).generate_summary(
        run_id=picked, force_refresh=refresh
    )
    if result.status == "available" and result.summary_text:
        return RunAiSummary(picked, result.summary_text)
    code = result.error_code or ""
    reason = _REASONS.get(code, f"No summary ({code or result.status})")
    details = [str(x) for x in result.limitations if x and " " in str(x)]
    if details:
        reason += ": " + "; ".join(details)
    return RunAiSummary(picked, None, reason)
