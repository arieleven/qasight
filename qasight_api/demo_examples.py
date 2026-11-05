"""Made-up examples for this repository's live Pages demo.

The demo is built without an AI key and its runs record no ``--release``, so
the AI failure summary, the AI review, the Ask page and the Releases page would
all be empty there. For the demo only (``scripts/export_static_site.py``, never
``qasight export-site``), the export fills them with examples built from the
demo's real runs. Every example is marked so the UI can say it is made up:

* AI answers carry ``provider == DEMO_EXAMPLE_PROVIDER``;
* example releases and Ask turns are listed in the manifest's ``examples``.

Nothing here calls an AI provider: the text is a template over the run data.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

DEMO_EXAMPLE_PROVIDER = "demo-example"
DEMO_EXAMPLE_MODEL = "made-up example, not a real AI answer"
# Newest first. The suffix keeps them from being mistaken for real QASight versions.
EXAMPLE_RELEASES = ("2.1-example", "2.0-example")
# Runs of each cycle that go into the newer example release; older ones go into the one before.
NEWER_RELEASE_RUNS = 2


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _short(message: str, limit: int = 140) -> str:
    line = (message or "").strip().splitlines()[0] if message else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def example_ai_summary(run_id: str, failures: dict[str, Any] | None) -> dict[str, Any]:
    """An ``AiSummaryResponse`` written from the run's failing tests, in the AI summary's shape."""
    tests = list((failures or {}).get("tests") or [])
    groups = list((failures or {}).get("groups") or [])
    if tests:
        names = ", ".join(t["name"] for t in tests[:3])
        more = f" and {len(tests) - 3} more" if len(tests) > 3 else ""
        what = f"{_plural(len(tests), 'test')} failed: {names}{more}."
    else:
        what = "No single test failed; a stage stopped before or outside its tests."
    if groups:
        top = groups[0]
        cause = (
            f'{_plural(int(top["count"]), "test")} share one error, "{_short(top["message"])}", '
            "so one fix probably clears them together."
        )
        if len(groups) > 1:
            cause += f" The rest split into {_plural(len(groups) - 1, 'other cause')}."
    elif tests:
        cause = f'"{_short(tests[0].get("message", ""))}" in {tests[0]["name"]}.'
    else:
        cause = "Check the stage log below for the first error."
    long_standing = sum(1 for t in tests if not t.get("new"))
    steps = (
        "Fix the shared error first, then rerun just these tests with the command above."
        if groups
        else "Open the first failing test and rerun it alone."
    )
    if long_standing:
        steps += f" {_plural(long_standing, 'test')} were already failing before this run."
    return {
        "schema_version": "v1",
        "run_id": run_id,
        "status": "available",
        "summary_text": f"What failed: {what}\nLikely cause: {cause}\nNext steps: {steps}",
        "confidence": "medium",
        "limitations": ["demo_example"],
        "provider": DEMO_EXAMPLE_PROVIDER,
        "model": DEMO_EXAMPLE_MODEL,
        "generated_at": time.time(),
        "context_stats": {"failing_tests": len(tests)},
        "error_code": None,
    }


def _signal(health: dict[str, Any], key: str) -> dict[str, Any]:
    return next((s for s in health.get("signals") or [] if s.get("key") == key), {})


def _grade(score: int) -> str:
    for floor, grade in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= floor:
            return grade
    return "F"


def example_ai_review(
    scope: str | None, health: dict[str, Any], skills: dict[str, Any]
) -> dict[str, Any]:
    """A ``ProjectReviewResponse`` whose scores follow the health signals, one result per skill."""
    findings = list(health.get("findings") or [])
    red = [f for f in findings if f.get("kind") == "red_cycle"]
    results: list[dict[str, Any]] = []
    for skill in skills.get("items") or []:
        if not skill.get("default_on"):
            continue
        sid = skill["id"]
        if sid == "reliability":
            sig = _signal(health, "stable")
            score = int(sig.get("score") or 80)
            summary = f"{sig.get('value', 'No flaky tests')}. Tests that fail every run are broken, not flaky."
            found = [
                {
                    "severity": "medium",
                    "title": "Broken tests mixed with flaky ones",
                    "detail": "Keep the always-red tests out of quarantine; they need a fix, not a retry.",
                }
            ]
        elif sid == "failures":
            score = 60 if red else 90
            summary = (
                "Most failures share a handful of error messages, so a few fixes clear most of the red."
                if red
                else "No cycle is red; nothing to group."
            )
            found = [
                {"severity": "high", "title": f["title"], "detail": f.get("detail", "")}
                for f in red[:2]
            ]
        elif sid == "speed":
            sig = _signal(health, "speed")
            score = int(sig.get("score") or 85)
            summary = f"{sig.get('value', 'No test got slower')}. One deliberately slow route dominates the suite time."
            found = [
                {
                    "severity": "low",
                    "title": "One slow test sets the pace",
                    "detail": "Give the slow route its own stage so the rest report sooner.",
                }
            ]
        elif sid == "strategy":
            sig = _signal(health, "pyramid")
            score = int(sig.get("score") or 80) - 8
            summary = f"{sig.get('value', 'Tests at several layers.')}".strip()
            found = [
                {
                    "severity": "low",
                    "title": "Few end-to-end tests guard the UI",
                    "detail": "The browser tests cover the buttons; add one for the error page.",
                }
            ]
        elif sid == "debt":
            sig = _signal(health, "hygiene")
            score = int(sig.get("score") or 80) - 15
            summary = f"{sig.get('value', 'Nothing skipped')}. Tests that stay red for many runs are the main debt."
            found = [
                {
                    "severity": "medium",
                    "title": "Long-red tests",
                    "detail": "A test red for weeks stops being read; fix it or delete it.",
                }
            ]
        else:
            score = 85
            summary = "The set of tests barely changed across the recent runs."
            found = []
        results.append(
            {
                "skill_id": sid,
                "name": skill["name"],
                "score": max(0, min(100, score)),
                "summary": summary,
                "findings": found,
            }
        )
    total = round(sum(r["score"] for r in results) / len(results)) if results else 0
    return {
        "scope": scope,
        "status": "available",
        "score": total,
        "grade": _grade(total),
        "summary": (
            "Example review: the suite is mostly healthy, and the red comes from a few tests "
            "that fail on purpose in this demo."
        ),
        "skills": results,
        "provider": DEMO_EXAMPLE_PROVIDER,
        "model": DEMO_EXAMPLE_MODEL,
        "generated_at": time.time(),
        "based_on_run_id": None,
        "stale": False,
        "error_code": None,
        "error_message": None,
        "context_stats": {},
    }


def example_ask_turns(
    attention: dict[str, Any] | None, health: dict[str, Any] | None
) -> dict[str, Any]:
    """Two question-and-answer turns for the Ask page, from the dashboard's own data."""
    attention = attention or {}
    failing = list(attention.get("failing") or [])
    if failing:
        lines = [
            f"{f['cycle']}: {_plural(int(f['failed_tests']), 'test')} failing"
            + (f", red for {f['failing_runs']} runs" if f.get("failing_runs", 0) > 1 else "")
            for f in failing[:3]
        ]
        first = "Start with " + "; then ".join(lines) + "."
    else:
        first = "Nothing is red: every cycle passed its latest run."
    flaky = list(attention.get("newly_flaky") or [])
    stable = _signal(health or {}, "stable").get("value")
    second = (
        "These started flaking: " + ", ".join(t["name"] for t in flaky[:3]) + "."
        if flaky
        else f"None right now ({stable or 'no flaky tests'}). The red tests fail every run, so they are broken, not flaky."
    )
    return {
        "items": [
            {"question": "What should I fix first?", "answer": first, "model": DEMO_EXAMPLE_MODEL},
            {"question": "Which tests are flaky?", "answer": second, "model": DEMO_EXAMPLE_MODEL},
        ]
    }


@contextmanager
def example_releases() -> Iterator[bool]:
    """Group the demo's runs into two made-up releases while the export reads ``/releases``.

    Yields whether it did: when a run already recorded a real release, those are
    exported as they are. Only the in-process read is changed; the history
    database is not written.
    """
    from qasight.history.read_model import latest_run_per_cycle, list_cycle_runs
    from qasight.services import releases

    if releases.list_releases():
        yield False
        return

    def grouped() -> dict[str, dict[str, list[Any]]]:
        newer, older = EXAMPLE_RELEASES
        out: dict[str, dict[str, list[Any]]] = {}
        for cycle in latest_run_per_cycle():
            runs = list_cycle_runs(cycle)[: NEWER_RELEASE_RUNS * 2 + 1]
            if runs[:NEWER_RELEASE_RUNS]:
                out.setdefault(newer, {})[cycle] = runs[:NEWER_RELEASE_RUNS]
            if runs[NEWER_RELEASE_RUNS:]:
                out.setdefault(older, {})[cycle] = runs[NEWER_RELEASE_RUNS:]
        return out

    original = releases._runs_by_release
    releases._runs_by_release = grouped  # type: ignore[assignment]
    try:
        yield bool(grouped())
    finally:
        releases._runs_by_release = original  # type: ignore[assignment]
