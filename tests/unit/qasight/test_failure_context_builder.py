from __future__ import annotations

from qasight.history.views import CompletedRunView
from qasight.repository.models import RunStatus
from qasight.services.failure_context_builder import FailureContextBudget, build_failure_context


def _run() -> CompletedRunView:
    return CompletedRunView(
        run_id="run-1",
        status=RunStatus.FAILED,
        created_at=1.0,
        started_at=1.0,
        finished_at=2.0,
        test_kind="pytest",
        returncode=1,
        wall_duration_ms=100.0,
        metrics_duration_ms=90,
        total_tests=10,
        passed=8,
        failed=2,
        broken=0,
        skipped=0,
        avg_case_ms=10.0,
        health_pct=80.0,
        target_repo=".",
        snapshot_dir=None,
        audit_json=None,
    )


def test_failure_context_applies_budget_truncation() -> None:
    context = build_failure_context(
        run=_run(),
        metadata={"error_message": "x" * 200},
        budget=FailureContextBudget(
            max_total_chars=120, max_log_chars=40, max_trace_chars=20, max_metadata_chars=20
        ),
    )
    assert context.context_stats["prompt_chars"] <= 120
    assert (
        "log_truncated" in context.limitations or "context_budget_truncated" in context.limitations
    )


def test_failure_context_redacts_secrets_from_prompt() -> None:
    context = build_failure_context(
        run=_run(),
        metadata={
            "error_message": "request failed with Bearer sk-test-secret-token",
            "traceback": "RuntimeError: api_key=supersecretvalue",
            "sync": "token=syncsecrettoken",
        },
        budget=FailureContextBudget(),
    )

    assert "***REDACTED***" in context.prompt
    assert "sk-test-secret-token" not in context.prompt
    assert "supersecretvalue" not in context.prompt
    assert "syncsecrettoken" not in context.prompt


def test_failure_context_prefers_specific_metadata_fields() -> None:
    context = build_failure_context(
        run=_run(),
        metadata={
            "error_message": "specific error message",
            "error": "generic error",
            "audit_json": "audit fallback trace",
        },
        budget=FailureContextBudget(),
    )

    assert "specific error message" in context.prompt
    assert "generic error" not in context.prompt
    assert "audit fallback trace" in context.prompt


def test_failure_context_uses_error_and_audit_fallbacks() -> None:
    context = build_failure_context(
        run=_run(),
        metadata={"error": "generic error", "audit_json": "audit fallback trace"},
        budget=FailureContextBudget(),
    )

    assert "generic error" in context.prompt
    assert "audit fallback trace" in context.prompt


def test_failure_context_redacts_structured_secret_metadata() -> None:
    context = build_failure_context(
        run=_run(),
        metadata={
            "error_message": "request failed with api_key=LOGSECRET123456",
            "traceback": "Authorization: Bearer trace-secret-123456",
            "sync": {
                "api_key": "SYNCSECRET123456",
                "nested": {
                    "authorization": "Bearer nested-secret-123456",
                    "safe": "retained diagnostic context",
                },
            },
        },
        budget=FailureContextBudget(),
    )

    assert "LOGSECRET123456" not in context.prompt
    assert "trace-secret-123456" not in context.prompt
    assert "SYNCSECRET123456" not in context.prompt
    assert "nested-secret-123456" not in context.prompt
    assert "retained diagnostic context" in context.prompt
    assert "***REDACTED***" in context.prompt


def test_failure_context_tells_the_model_about_a_detected_crash() -> None:
    context = build_failure_context(
        run=_run(),
        metadata={"crash": "Stack overflow (Go): fatal error: stack overflow"},
        budget=FailureContextBudget(),
    )

    assert "crash_detected=Stack overflow (Go): fatal error: stack overflow" in context.prompt


def test_failure_context_has_no_crash_line_without_one() -> None:
    context = build_failure_context(run=_run(), metadata={}, budget=FailureContextBudget())

    assert "crash_detected" not in context.prompt
