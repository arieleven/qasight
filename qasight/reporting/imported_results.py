"""Shared helpers for the result-file importers (Jest/Vitest JSON, TRX, ``go test -json``, Robot).

Each importer turns one runner's own result file into Allure ``*-result.json``
dicts, the same shape :mod:`qasight.reporting.junit_import` writes, so the
rest of QASight (summaries, history, Compare, reporters) never needs to
know which runner produced them.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

MAX_TRACE_CHARS = 20_000


def clip(text: Any) -> str | None:
    """``text`` stripped and capped at :data:`MAX_TRACE_CHARS`; ``None`` when empty.

    A non-string (a report with an odd field type) is shown as its ``str()``.
    """
    if not text:
        return None
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()
    if len(text) > MAX_TRACE_CHARS:
        text = text[:MAX_TRACE_CHARS] + "\n… (truncated)"
    return text or None


def iso_ms(value: Any, fallback_ms: int) -> int:
    """Epoch milliseconds from an ISO 8601 timestamp, else ``fallback_ms``."""
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(parsed.timestamp() * 1000)
        except ValueError:
            pass
    return fallback_ms


def make_result(
    *,
    tool: str,
    framework: str,
    language: str,
    name: str,
    full_name: str,
    status: str,
    start_ms: int,
    duration_ms: int,
    suite: str,
    message: str | None = None,
    trace: str | None = None,
    labels: list[dict[str, str]] | None = None,
    flaky: bool = False,
) -> dict[str, Any]:
    """One Allure result dict. ``historyId`` hashes ``tool`` and ``full_name`` like the JUnit importer."""
    result: dict[str, Any] = {
        "uuid": str(uuid.uuid4()),
        "historyId": hashlib.sha256(f"{tool}:{full_name}".encode()).hexdigest(),
        "name": name,
        "fullName": full_name,
        "status": status,
        "stage": "finished",
        "start": start_ms,
        "stop": start_ms + max(0, duration_ms),
        "labels": [
            {"name": "suite", "value": suite},
            {"name": "framework", "value": framework},
            {"name": "language", "value": language},
            *(labels or []),
        ],
    }
    message, trace = clip(message), clip(trace)
    if message:
        message = message.splitlines()[0]
    details: dict[str, Any] = {
        k: v for k, v in (("message", message), ("trace", trace or message)) if v
    }
    if flaky:
        details["flaky"] = True
    if details:
        result["statusDetails"] = details
    return result


# Worst first: which status a test ends with when its attempts disagree.
_RANK = {"failed": 0, "broken": 1, "passed": 2, "skipped": 3}


def fold_attempts(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One result per test when a report lists the same test more than once.

    Runners write a test twice when it ran twice: pytest's JUnit report adds a
    second ``<testcase>`` for an error in teardown, ``go test -count=N`` runs
    each test N times. Results with the same ``historyId`` become one, at the
    first one's place:

    - a failure followed by a final pass is ``passed`` and flaky, with the
      first failure's message ("Passed on rerun. ...");
    - otherwise the worst status wins (failed, broken, passed, skipped), with
      that attempt's message and trace.

    The result spans all attempts (earliest start to latest stop).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        groups.setdefault(str(result.get("historyId")), []).append(result)
    folded: list[dict[str, Any]] = []
    for result in results:
        attempts = groups.pop(str(result.get("historyId")), None)
        if attempts is None:
            continue  # already folded into an earlier one
        if len(attempts) == 1:
            folded.append(result)
            continue
        problems = [a for a in attempts if a.get("status") in ("failed", "broken")]
        if problems and attempts[-1].get("status") == "passed":
            first = problems[0].get("statusDetails") or {}
            why = str(first.get("message") or "").strip()
            details: dict[str, Any] = {
                "flaky": True,
                "message": clip(f"Passed on rerun. {why}".strip()),
            }
            if first.get("trace"):
                details["trace"] = first["trace"]
            pick = dict(attempts[-1], statusDetails=details)
        else:
            worst = min(attempts, key=lambda a: _RANK.get(str(a.get("status")), 2))
            pick = dict(worst)
        pick["start"] = min(int(a.get("start") or 0) for a in attempts)
        pick["stop"] = max(int(a.get("stop") or 0) for a in attempts)
        pick["uuid"] = result["uuid"]
        folded.append(pick)
    return folded
