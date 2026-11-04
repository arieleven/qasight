"""Failure evidence for a failed :class:`PlanResult`, stored on its run record.

The AI failure analysis (:mod:`qasight.services.failure_context_builder`)
reads ``error_message`` and ``traceback`` from run metadata. This module fills
them from what the engine already left on disk: each stage's Allure results
(failed/broken cases) and the failing stage's captured output tail.
All text is redacted before it is stored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qasight.engine.crash_signatures import crash_in_text
from qasight.engine.result import PlanResult
from qasight.security.redaction import redact_text


def failed_cases_from_allure(
    *,
    results_dir: Path,
    max_cases: int = 20,
    message_max_chars: int = 2000,
    trace_max_chars: int = 4000,
) -> tuple[list[dict[str, Any]], str | None]:
    """Collect failed/broken cases from an Allure ``results_dir``.

    Returns ``(cases, trace_excerpt)``: each case has ``name``/``fullName``/
    ``status``/``message``; ``trace_excerpt`` is the first traceback seen,
    trimmed to ``trace_max_chars``. Malformed result files are skipped.
    """
    if not results_dir.is_dir():
        return [], None

    cases: list[dict[str, Any]] = []
    trace_excerpt: str | None = None
    for path in sorted(results_dir.glob("*-result.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        status = str(payload.get("status") or "").lower()
        if status not in {"failed", "broken"}:
            continue
        details = payload.get("statusDetails") or {}
        trace_raw = str(details.get("trace") or "")
        if trace_excerpt is None and trace_raw:
            trace_excerpt = redact_text(trace_raw)[:trace_max_chars]
        case = {
            "name": str(payload.get("name") or ""),
            "fullName": str(payload.get("fullName") or ""),
            "status": status,
            "message": redact_text(str(details.get("message") or ""))[:message_max_chars],
        }
        crash = crash_in_text(f"{details.get('message') or ''}\n{trace_raw}")
        if crash is not None:
            case["crash"] = redact_text(crash.describe())
        cases.append(case)
        if len(cases) >= max_cases:
            break
    return cases, trace_excerpt


def failure_metadata(
    result: PlanResult, *, max_cases: int = 20, log_tail_chars: int = 4000
) -> dict[str, Any]:
    """Run-metadata keys describing why *result* failed; ``{}`` when it passed.

    Keys (each only when there is evidence for it): ``failure_context``
    (``schema_version``/``captured_cases``/``failed_cases``, each case tagged
    with its ``stage``), ``error_message``, ``traceback``, ``log_tail``, and
    ``crash`` (the first stack overflow or crash a stage or test showed).
    """
    if result.exit_code == 0:
        return {}

    cases: list[dict[str, Any]] = []
    trace: str | None = None
    for stage in result.stages:
        if len(cases) >= max_cases:
            break
        stage_cases, stage_trace = failed_cases_from_allure(
            # Layout written by executor.run_stage(): <stage_dir>/allure-results/<framework>/
            results_dir=stage.artifacts_dir / "allure-results" / stage.framework,
            max_cases=max_cases - len(cases),
        )
        cases.extend({**case, "stage": stage.stage_name} for case in stage_cases)
        trace = trace or stage_trace

    out: dict[str, Any] = {}
    if cases:
        out["failure_context"] = {
            "schema_version": "v1",
            "captured_cases": len(cases),
            "failed_cases": cases,
        }
        if cases[0]["message"]:
            out["error_message"] = cases[0]["message"]
    if trace:
        out["traceback"] = trace

    failing_stage = next((s for s in result.stages if s.returncode != 0), None)
    if failing_stage is not None:
        if failing_stage.timed_out:
            out["error"] = "timeout"
        if failing_stage.error:
            out.setdefault("error_message", redact_text(failing_stage.error))
        if failing_stage.output_tail:
            tail = redact_text(failing_stage.output_tail)[-log_tail_chars:]
            out["log_tail"] = tail
            out.setdefault("error_message", tail)
    if result.error:
        out.setdefault("error_message", redact_text(str(result.error)))
    crash = next((redact_text(s.crash_detail) for s in result.stages if s.crash_detail), None)
    crash = crash or next((c["crash"] for c in cases if c.get("crash")), None)
    if crash:
        out["crash"] = crash
    return out
