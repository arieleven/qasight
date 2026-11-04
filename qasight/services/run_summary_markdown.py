"""A run as GitHub-flavored Markdown: a job summary or a pull request comment.

``qasight report markdown`` prints it; the GitHub Action writes it to the job
summary and, when asked, keeps one comment per cycle on the pull request up to
date (the first line is a hidden marker it finds that comment by).

Everything comes from stored run records and their artifact snapshots, so the
same text can be rebuilt for any past run.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from qasight.engine.crash_signatures import label as crash_label
from qasight.history.cases import cases_for_run
from qasight.history.read_model import get_run, list_cycle_runs, list_recent_runs
from qasight.history.views import CompletedRunView
from qasight.services.report_archive_diff import CaseChange, diff_case_maps

MAX_ROWS = 20
_FAILED = frozenset({"failed", "broken"})
_CHANGE_ORDER = {"regression": 0, "added": 1, "fix": 2, "removed": 3, "status_change": 4}
_CHANGE_TALLY = {
    "regression": ("regression", "regressions"),
    "fix": ("fixed", "fixed"),
    "added": ("new test", "new tests"),
    "removed": ("removed", "removed"),
    "status_change": ("changed", "changed"),
}
_CHANGE_LABEL = {
    "regression": "🔴 regression",
    "fix": "🟢 fixed",
    "added": "🆕 new",
    "removed": "➖ removed",
    "status_change": "🔁 changed",
}


def comment_marker(cycle: str | None) -> str:
    """Hidden first line that identifies this cycle's comment on a pull request."""
    # "--" and ">" would end the HTML comment early; plain cycle names are unchanged.
    safe = re.sub(r"-{2,}|>", "_", cycle or "-")
    return f"<!-- testo-summary:{safe} -->"


@dataclass(frozen=True)
class RunSummary:
    run: CompletedRunView
    previous: CompletedRunView | None
    failing: tuple[str, ...]
    changes: tuple[CaseChange, ...]
    # The branch ``previous`` was taken from; ``None`` means simply the run before.
    baseline_branch: str | None = None
    # False when ``previous`` has no stored test results (it crashed, or its
    # snapshot was pruned): then "no test changed" would be a guess.
    compared: bool = True
    # Failing test name -> "stack_overflow" | "crash" (engine/crash_signatures.py).
    crashes: Mapping[str, str] = field(default_factory=dict)
    # Tests TESTO_QUARANTINE_AUTO quarantined after this run.
    auto_quarantined: tuple[str, ...] = ()

    @property
    def baseline_label(self) -> str:
        if self.baseline_branch:
            return f"the last run on {_code(self.baseline_branch)}"
        return "the previous run"


def _cases(run: CompletedRunView | None) -> dict[str, dict[str, Any]]:
    return cases_for_run(run) if run is not None else {}


def _cycle_runs(cycle: str, *, before_run_id: str | None = None) -> list[CompletedRunView]:
    return list_cycle_runs(cycle, before_run_id=before_run_id)


def pick_baseline(
    run: CompletedRunView, older: list[CompletedRunView], branch: str | None
) -> tuple[CompletedRunView | None, str | None]:
    """The run to compare ``run`` with, newest first in ``older``, and the branch it came from.

    A pull request is judged against its target branch (``branch``, else the
    run's recorded ``base_branch``), so a parallel PR's run never becomes the
    baseline. Without a target, or with no run on it yet, it is the previous
    run on the run's own branch (any previous run when the run has no branch
    recorded): a push to ``main`` is never compared with a feature branch's run.
    """
    target = branch or run.base_branch
    if target and target != run.branch:
        on_target = next((r for r in older if r.branch == target), None)
        if on_target is not None:
            return on_target, target
    if run.branch:
        return next((r for r in older if r.branch == run.branch), None), None
    return (older[0] if older else None), None


def load_run_summary(
    *,
    cycle: str | None = None,
    run_id: str | None = None,
    baseline_branch: str | None = None,
) -> RunSummary | None:
    """The given run (or ``cycle``'s latest) with its failures and changes since its baseline."""
    if run_id:
        run = get_run(run_id=run_id)
        if run is None:
            return None
        older = _cycle_runs(run.cycle, before_run_id=run.run_id) if run.cycle else []
    elif cycle:
        runs = _cycle_runs(cycle)
        if not runs:
            return None
        run, older = runs[0], runs[1:]
    else:
        recent = list_recent_runs(limit=1)
        if not recent:
            return None
        return load_run_summary(run_id=recent[0].run_id, baseline_branch=baseline_branch)
    previous, from_branch = pick_baseline(run, older, baseline_branch)

    current_cases = _cases(run)
    failing = tuple(
        sorted(str(c["name"]) for c in current_cases.values() if c["status"] in _FAILED)
    )
    crashes = {
        str(c["name"]): str(c["crash"])
        for c in current_cases.values()
        if c["status"] in _FAILED and c.get("crash")
    }
    changes: tuple[CaseChange, ...] = ()
    compared = False
    if previous is not None:
        previous_cases = _cases(previous)
        compared = bool(previous_cases) or not current_cases
        if previous_cases and current_cases:
            changes = tuple(
                sorted(
                    diff_case_maps(previous_cases, current_cases),
                    key=lambda c: (_CHANGE_ORDER.get(c.kind, 9), c.name),
                )
            )
    auto_quarantined: tuple[str, ...] = ()
    from qasight.services.quarantine_store import auto_quarantined_since, auto_threshold

    if run.cycle and auto_threshold() > 0:
        auto_quarantined = tuple(auto_quarantined_since(run.cycle, run.started_at))
    return RunSummary(
        run=run,
        previous=previous,
        failing=failing,
        changes=changes,
        baseline_branch=from_branch,
        compared=compared,
        crashes=crashes,
        auto_quarantined=auto_quarantined,
    )


def _stage_names(run: CompletedRunView) -> list[str]:
    return [str(s.get("name")) for s in run.stage_health if s.get("name")]


def stage_change_note(run: CompletedRunView, previous: CompletedRunView | None) -> str | None:
    """A sentence when *previous* recorded the cycle with other stages than *run*.

    That usually means the suite was recorded a different way (``qasight run`` one
    time, ``pytest --qasight`` or ``qasight ingest`` the next). Tests are still
    matched by name (:func:`~qasight.services.report_archive_diff.align_case_keys`),
    but durations, flaky history and stage results are per stage.
    """
    if previous is None:
        return None
    now, before = _stage_names(run), _stage_names(previous)
    if not now or not before or set(now) == set(before):
        return None
    return (
        f"The stages changed since the run it is compared with ({', '.join(before)} → "
        f"{', '.join(now)}). If the same tests were recorded another way (qasight run, "
        "pytest --qasight, qasight ingest), keep one way per cycle so their history stays together."
    )


def _seconds(ms: float | None) -> str:
    if ms is None:
        return "—"
    return f"{ms / 1000:.1f}s"


def _count(value: Any) -> str:
    return "—" if value is None else str(value)


def _escape(text: str) -> str:
    """Plain text for a table cell: one line, ``|`` escaped, no live HTML or @mention."""
    text = " ".join(str(text).split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("@", "&#64;").replace("|", "\\|")


def _code(text: str, *, in_table: bool = False) -> str:
    """``text`` as one inline code span that nothing in it can close.

    Test names come from the code under test, in a fork PR from its author: a
    backtick in one used to end the span, so ``@someone`` or ``<img>`` after it
    rendered live. The fence is one backtick longer than the longest run inside.
    """
    text = " ".join(str(text).split())
    if in_table:
        text = text.replace("|", "\\|")  # GitHub reads \| as | even inside a code span
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def coverage_text(run: CompletedRunView, previous: CompletedRunView | None) -> str:
    """``coverage 81.2% (▼ 0.4 vs baseline)``, or ``""`` when the run stored no coverage."""
    if run.coverage_pct is None:
        return ""
    text = f"coverage {run.coverage_pct:.1f}%"
    if previous is None or previous.coverage_pct is None:
        return text
    delta = round(run.coverage_pct - previous.coverage_pct, 1)
    if delta == 0:
        return f"{text} (unchanged)"
    arrow = "▲" if delta > 0 else "▼"
    return f"{text} ({arrow} {abs(delta):.1f})"


def render_markdown(summary: RunSummary, *, run_url: str | None = None) -> str:
    """Markdown for one run: headline, per-stage table, failing tests, changes since last run."""
    run = summary.run
    ok = run.returncode == 0
    name = run.cycle or "run"
    lines = [
        comment_marker(run.cycle),
        f"### {'✅' if ok else '❌'} QASight: {_code(name)} {'passed' if ok else 'failed'}",
        "",
    ]

    headline = f"Ran in {_seconds(run.wall_duration_ms)}"
    if run.total_tests is not None:
        failed = (run.failed or 0) + (run.broken or 0)
        headline = (
            f"**{run.passed or 0} passed · {failed} failed · {run.skipped or 0} skipped** "
            f"in {_seconds(run.wall_duration_ms)}"
        )
    if run.health_pct is not None:
        headline += f" · pass rate {run.health_pct:.1f}%"
    coverage = coverage_text(run, summary.previous)
    if coverage:
        headline += f" · {coverage}"
    lines += [headline, ""]

    if run.stage_health:
        lines += [
            "| Stage | Runner | Result | Tests | Passed | Failed | Skipped | Time |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
        for s in run.stage_health:
            failed = (s.get("failed") or 0) + (s.get("broken") or 0)
            result = "✅ pass" if s.get("returncode") == 0 else "❌ fail"
            if s.get("quarantined"):
                result += f" · {len(s['quarantined'])} quarantined"
            if s.get("crash_kind"):
                result += f" · 💥 {crash_label(str(s['crash_kind'])).lower()}"
            duration = s.get("duration_s")
            lines.append(
                f"| {_escape(str(s.get('name', '?')))} | {_escape(str(s.get('framework') or ''))} "
                f"| {result} "
                f"| {_count(s.get('total_tests'))} | {_count(s.get('passed'))} | {failed} "
                f"| {_count(s.get('skipped'))} | {_seconds(duration * 1000 if duration is not None else None)} |"
            )
        lines.append("")

    if summary.failing:
        lines.append(f"<details{' open' if len(summary.failing) <= 5 else ''}>")
        lines.append(f"<summary><b>Failing tests ({len(summary.failing)})</b></summary>")
        lines.append("")
        quarantined = {str(n) for s in run.stage_health for n in (s.get("quarantined") or []) if n}
        lines += [
            f"- {_code(n)}"
            + (f" 💥 _{crash_label(summary.crashes[n]).lower()}_" if n in summary.crashes else "")
            + (" _(quarantined)_" if n in quarantined else "")
            for n in summary.failing[:MAX_ROWS]
        ]
        if len(summary.failing) > MAX_ROWS:
            lines.append(f"- … and {len(summary.failing) - MAX_ROWS} more")
        lines += ["", "</details>", ""]

    if summary.previous is None:
        lines.append("_First recorded run of this cycle, so there is nothing to compare with yet._")
    elif not summary.changes and not summary.compared:
        lines.append(
            f"_No test results are stored for {summary.baseline_label}, so there is nothing to"
            " compare with._"
        )
    elif not summary.changes:
        lines.append(f"No test changed outcome since {summary.baseline_label}.")
    else:
        kinds: list[str] = [c.kind for c in summary.changes]
        tally = ", ".join(
            f"{n} {_CHANGE_TALLY[k][0 if n == 1 else 1]}"
            for k in _CHANGE_ORDER
            if (n := kinds.count(k))
        )
        lines += [
            f"**Since {summary.baseline_label}:** {tally}",
            "",
            "| Change | Test | Before | After |",
            "|---|---|---|---|",
        ]
        for c in summary.changes[:MAX_ROWS]:
            lines.append(
                f"| {_CHANGE_LABEL.get(c.kind, c.kind)} | {_code(c.name, in_table=True)} "
                f"| {c.baseline_status or '—'} | {c.current_status or '—'} |"
            )
        if len(summary.changes) > MAX_ROWS:
            lines.append(f"| | … and {len(summary.changes) - MAX_ROWS} more | | |")
    if summary.auto_quarantined:
        names = ", ".join(_code(n) for n in summary.auto_quarantined[:MAX_ROWS])
        lines += [
            "",
            f"🔇 **Quarantined automatically** (flaky, failed here): {names}. From the next run "
            "on their failures don't fail the build; they're released after enough clean runs.",
        ]
    note = stage_change_note(run, summary.previous)
    if note:
        lines += ["", f"_{note}_"]
    lines.append("")

    footer = f"<sub>Run `{run.run_id}`"
    if run_url:
        footer += f" · [workflow run]({run_url})"
    footer += " · generated by [QASight](https://github.com/arieleven/qasight)</sub>"
    lines.append(footer)
    return "\n".join(lines) + "\n"


def ai_section(markdown: str, summary: str | None, reason: str = "") -> str:
    """*markdown* with the AI failure summary added before its footer line.

    The summary is model output about test logs that a pull request's author can
    write to, so it goes in a quote with HTML and mentions neutralised, like test names.
    """
    if summary:
        lines_out = []
        for line in str(summary).strip().splitlines():
            line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines_out.append(f"> {line.replace('@', '&#64;')}" if line.strip() else ">")
        body = "\n".join(lines_out)
        block = f"#### AI summary of the failures\n\n{body}\n"
    else:
        block = f"_No AI summary: {_escape(reason) or 'the provider did not answer'}._\n"
    lines = markdown.rstrip("\n").split("\n")
    footer = lines.pop() if lines and lines[-1].startswith("<sub>") else ""
    return "\n".join([*lines, block, footer]).rstrip("\n") + "\n"
