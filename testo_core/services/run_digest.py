"""A weekly (or daily) digest of every cycle, for a Slack or Microsoft Teams channel.

Per-run alerts (:mod:`testo_core.services.run_alerts`) say what one run did. The
digest says how the week went, for a team lead who doesn't read every alert: runs
and green builds against the week before, the test pass rate, tests that turned
flaky, tests that got fixed, tests still failing, and the ones that got slower.

It reads the same stored runs and per-run result snapshots as ``testo flaky`` and
the test page. ``testo notify --digest week`` sends it; run that from cron or a
scheduled CI job, as Testosterone has no scheduler of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median
from typing import Any

from testo_core.history.read_model import list_recent_runs
from testo_core.history.views import CompletedRunView
from testo_core.services import flaky_service
from testo_core.services.delta_service import within_noise
from testo_core.services.run_alerts import AlertChannel, _slack_escape

PERIODS: dict[str, int] = {"day": 1, "week": 7}
MAX_NAMES = 5
# Below this a per-test slowdown is jitter, whatever its percentage (as on the test page).
SLOWER_FLOOR_MS = 20.0
_SCAN_LIMIT = 2000
_FAIL = frozenset({"failed", "broken"})


@dataclass(frozen=True)
class Slower:
    name: str
    before_ms: int
    after_ms: int


@dataclass(frozen=True)
class CycleDigest:
    cycle: str
    runs: int
    green: int
    prev_runs: int
    prev_green: int
    pass_rate: float | None
    prev_pass_rate: float | None
    newly_flaky: tuple[str, ...] = ()
    fixed: tuple[str, ...] = ()
    still_failing: tuple[str, ...] = ()
    slower: tuple[Slower, ...] = ()
    # Line coverage of the newest run that stored it, this period and the one before.
    coverage_pct: float | None = None
    prev_coverage_pct: float | None = None


@dataclass(frozen=True)
class Digest:
    period: str
    start: float
    end: float
    cycles: tuple[CycleDigest, ...]


def _pass_rate(runs: list[CompletedRunView]) -> float | None:
    passed = sum(r.passed or 0 for r in runs)
    total = passed + sum((r.failed or 0) + (r.broken or 0) for r in runs)
    return round(100.0 * passed / total, 1) if total else None


def _coverage(runs: list[CompletedRunView]) -> float | None:
    return next((r.coverage_pct for r in runs if r.coverage_pct is not None), None)


def _cases(runs: list[CompletedRunView]) -> list[dict[str, dict[str, Any]]]:
    """Each run's per-test cases, oldest run first (runs without a snapshot are left out)."""
    out = [flaky_service._cases_for_run(r) for r in reversed(runs)]
    return [c for c in out if c]


def _flaky(cases: list[dict[str, dict[str, Any]]]) -> dict[str, str]:
    per_test: dict[str, list[dict[str, Any]]] = {}
    for run_cases in cases:
        for key, case in run_cases.items():
            per_test.setdefault(key, []).append(case)
    return {key: t.name for key, obs in per_test.items() if (t := flaky_service.classify(key, obs))}


def _failing(run_cases: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {
        key: str(case.get("name") or key)
        for key, case in run_cases.items()
        if str(case.get("status")) in _FAIL
    }


def _durations(cases: list[dict[str, dict[str, Any]]]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for run_cases in cases:
        for key, case in run_cases.items():
            ms = case.get("duration_ms")
            if isinstance(ms, int) and str(case.get("status")) == "passed":
                out.setdefault(key, []).append(ms)
    return out


def _slower(
    now_cases: list[dict[str, dict[str, Any]]], prev_cases: list[dict[str, dict[str, Any]]]
) -> tuple[Slower, ...]:
    names = {k: str(c.get("name") or k) for run in now_cases for k, c in run.items()}
    before = _durations(prev_cases)
    after = _durations(now_cases)
    found: list[Slower] = []
    for key, recent in after.items():
        earlier = before.get(key)
        if not earlier:
            continue
        a, b = int(median(recent)), int(median(earlier))
        if a <= b or within_noise(absolute_delta=a - b, baseline=b, floor_ms=SLOWER_FLOOR_MS):
            continue
        found.append(Slower(name=names.get(key, key), before_ms=b, after_ms=a))
    found.sort(key=lambda s: (-(s.after_ms - s.before_ms), s.name))
    return tuple(found[:MAX_NAMES])


def _cycle_digest(
    cycle: str, now_runs: list[CompletedRunView], prev_runs: list[CompletedRunView]
) -> CycleDigest:
    now_cases = _cases(now_runs)
    prev_cases = _cases(prev_runs)
    flaky_now = _flaky(now_cases)
    flaky_before = _flaky(prev_cases)
    latest = now_cases[-1] if now_cases else {}
    failing_now = _failing(latest)
    failing_before = _failing(prev_cases[-1]) if prev_cases else {}
    fixed = sorted(
        name
        for key, name in failing_before.items()
        if key in latest and str(latest[key].get("status")) == "passed"
    )
    return CycleDigest(
        cycle=cycle,
        runs=len(now_runs),
        green=sum(1 for r in now_runs if r.returncode == 0),
        prev_runs=len(prev_runs),
        prev_green=sum(1 for r in prev_runs if r.returncode == 0),
        pass_rate=_pass_rate(now_runs),
        prev_pass_rate=_pass_rate(prev_runs),
        newly_flaky=tuple(sorted(n for k, n in flaky_now.items() if k not in flaky_before)),
        fixed=tuple(fixed),
        still_failing=tuple(sorted(failing_now.values())),
        slower=_slower(now_cases, prev_cases),
        coverage_pct=_coverage(now_runs),
        prev_coverage_pct=_coverage(prev_runs),
    )


def build_digest(
    *,
    period: str = "week",
    cycles: list[str] | None = None,
    now: float | None = None,
    days: int | None = None,
) -> Digest:
    """Every cycle with a run in the last ``period`` (``day`` or ``week``), busiest first.

    ``days`` overrides the period's length (the manager report's month and quarter).
    """
    days = days if days is not None else PERIODS[period]
    end = now if now is not None else datetime.now(tz=UTC).timestamp()
    start = end - days * 86400
    prev_start = start - days * 86400
    wanted = set(cycles or ())
    by_cycle: dict[str, tuple[list[CompletedRunView], list[CompletedRunView]]] = {}
    for run in list_recent_runs(limit=_SCAN_LIMIT):  # newest first
        if not run.cycle or (wanted and run.cycle not in wanted):
            continue
        if not prev_start <= run.created_at <= end:
            continue
        now_runs, prev_runs = by_cycle.setdefault(run.cycle, ([], []))
        (now_runs if run.created_at >= start else prev_runs).append(run)
    digests = [
        _cycle_digest(cycle, now_runs, prev_runs)
        for cycle, (now_runs, prev_runs) in by_cycle.items()
        if now_runs
    ]
    digests.sort(key=lambda d: (-d.runs, d.cycle))
    return Digest(period=period, start=start, end=end, cycles=tuple(digests))


# -- messages ----------------------------------------------------------------------


def _day(ts: float) -> str:
    when = datetime.fromtimestamp(ts, tz=UTC)
    return f"{when:%b} {when.day}"


def title(digest: Digest) -> str:
    label = "Weekly" if digest.period == "week" else "Daily"
    span = (
        f"{_day(digest.start)} – {_day(digest.end)}"
        if digest.period == "week"
        else _day(digest.end)
    )
    return f"📅 {label} test digest, {span}"


def _change(now: float | None, before: float | None) -> str:
    if now is None:
        return ""
    if before is None:
        return f"{now:.1f}%"
    delta = now - before
    trend = "same as" if abs(delta) < 0.05 else ("up from" if delta > 0 else "down from")
    return f"{now:.1f}% ({trend} {before:.1f}%)"


def headline(d: CycleDigest, period: str) -> str:
    """``42 runs, 38 green (90%); tests 98.2% passed (up from 97.0%)``."""
    green_pct = round(100.0 * d.green / d.runs) if d.runs else 0
    before = ""
    if d.prev_runs:
        prev_pct = round(100.0 * d.prev_green / d.prev_runs)
        before = f", last {period} {d.prev_runs} runs, {prev_pct}% green"
    rate = _change(d.pass_rate, d.prev_pass_rate)
    tests = f"; tests {rate} passed" if rate else ""
    cov = _change(d.coverage_pct, d.prev_coverage_pct)
    coverage = f"; line coverage {cov}" if cov else ""
    return f"{d.runs} runs, {d.green} green ({green_pct}%{before}){tests}{coverage}"


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.2f}s" if ms < 10_000 else f"{ms / 1000:.0f}s"


def sections(d: CycleDigest) -> list[tuple[str, list[str]]]:
    """(heading, lines) for every non-empty list, most urgent first."""
    out = [
        ("Turned flaky", list(d.newly_flaky)),
        ("Still failing", list(d.still_failing)),
        ("Fixed", list(d.fixed)),
        (
            "Got slower",
            [f"{s.name}: {_seconds(s.before_ms)} → {_seconds(s.after_ms)}" for s in d.slower],
        ),
    ]
    return [(h, lines) for h, lines in out if lines]


def _clip(lines: list[str]) -> list[str]:
    extra = len(lines) - MAX_NAMES
    return lines[:MAX_NAMES] + ([f"… and {extra} more"] if extra > 0 else [])


def slack_payload(digest: Digest, *, url: str | None = None) -> dict[str, Any]:
    heading = title(digest)
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": heading[:150], "emoji": True}}
    ]
    if not digest.cycles:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "No runs were recorded."}}
        )
    for d in digest.cycles:
        text = f"*{_slack_escape(d.cycle)}*: {headline(d, digest.period)}"
        for label, lines in sections(d):
            items = "\n".join(f"• `{_slack_escape(x)}`" for x in _clip(lines))
            text += f"\n*{label} ({len(lines)})*\n{items}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}})
    if url:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"<{url}|Open Testosterone>"}],
            }
        )
    summary = f"{len(digest.cycles)} cycles" if digest.cycles else "no runs"
    return {"text": f"{heading}: {summary}", "blocks": blocks[:50]}


def teams_payload(digest: Digest, *, url: str | None = None) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": title(digest),
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        }
    ]
    if not digest.cycles:
        body.append({"type": "TextBlock", "text": "No runs were recorded.", "wrap": True})
    for d in digest.cycles:
        body.append(
            {"type": "TextBlock", "text": f"**{d.cycle}**", "wrap": True, "spacing": "Medium"}
        )
        body.append({"type": "TextBlock", "text": headline(d, digest.period), "wrap": True})
        for label, lines in sections(d):
            body.append({"type": "TextBlock", "text": f"**{label} ({len(lines)})**", "wrap": True})
            body.append(
                {
                    "type": "TextBlock",
                    "text": "\n".join(f"- {x}" for x in _clip(lines)),
                    "wrap": True,
                }
            )
    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }
    if url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open Testosterone", "url": url}]
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
        ],
    }


def build_payload(
    channel: AlertChannel, digest: Digest, *, url: str | None = None
) -> dict[str, Any]:
    return (slack_payload if channel == "slack" else teams_payload)(digest, url=url)
