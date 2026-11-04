"""Convert RSpec's JSON formatter output into Allure ``*-result.json`` dicts.

``rspec --format json --out rspec.json`` is built into RSpec, so Ruby suites
need no ``rspec_junit_formatter``. The file has one entry per example
(``{"description", "full_description", "status", "file_path", "line_number",
"run_time", "exception"}``) and a ``summary``.

Status mapping:

    passed      -> passed
    failed      -> failed
    pending     -> skipped

Errors outside examples (a spec file that failed to load, a ``before(:suite)``
that raised) have no example of their own: when the summary counts any, they
become one ``broken`` result with RSpec's messages as its trace.

``full_name`` is ``spec file › full description`` (``spec/cart_spec.rb › Cart
adds tax``): the two things ``rspec <file> -e <description>`` takes to rerun it.
"""

from __future__ import annotations

from typing import Any

from qasight.reporting.imported_results import make_result

_STATUS = {"passed": "passed", "failed": "failed", "pending": "skipped"}


def is_rspec_report(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("examples"), list)
        and ("summary" in data or "summary_line" in data)
    )


def _seconds_ms(value: Any) -> int:
    try:
        return max(0, int(round(float(value or 0) * 1000)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _spec_file(example: dict[str, Any]) -> str:
    path = str(example.get("file_path") or "")
    return path[2:] if path.startswith("./") else path


def rspec_results_for_report(
    data: dict[str, Any], *, tool: str, fallback_ms: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    clock = fallback_ms
    for example in data.get("examples") or []:
        if not isinstance(example, dict):
            continue
        status = _STATUS.get(str(example.get("status") or ""), "broken")
        description = str(example.get("description") or example.get("id") or "(unnamed)")
        full_description = str(example.get("full_description") or description)
        spec_file = _spec_file(example)
        duration = _seconds_ms(example.get("run_time"))
        message = trace = None
        exception = example.get("exception")
        if status != "passed" and isinstance(exception, dict):
            text = str(exception.get("message") or "").strip()
            message = f"{exception.get('class') or 'Error'}: {text}" if text else None
            backtrace = exception.get("backtrace") or []
            trace = "\n".join([text, *(str(line) for line in backtrace)]).strip() or None
        elif status == "skipped":
            message = example.get("pending_message") or None
        line = example.get("line_number")
        results.append(
            make_result(
                tool=tool,
                framework="rspec",
                language="ruby",
                name=description,
                full_name=f"{spec_file} › {full_description}" if spec_file else full_description,
                status=status,
                start_ms=clock,
                duration_ms=duration,
                suite=spec_file or "rspec",
                message=message,
                trace=trace,
                labels=[{"name": "line", "value": str(line)}] if line else [],
            )
        )
        clock += duration

    summary: dict[str, Any] = data["summary"] if isinstance(data.get("summary"), dict) else {}
    try:
        outside = int(summary.get("errors_outside_of_examples_count") or 0)
    except (TypeError, ValueError):
        outside = 0
    if outside:
        messages = [str(m) for m in data.get("messages") or []]
        results.append(
            make_result(
                tool=tool,
                framework="rspec",
                language="ruby",
                name="(errors outside of examples)",
                full_name="(errors outside of examples)",
                status="broken",
                start_ms=fallback_ms,
                duration_ms=0,
                suite="rspec",
                message=f"{outside} error(s) occurred outside of examples",
                trace="\n".join(messages) or None,
            )
        )
    return results
