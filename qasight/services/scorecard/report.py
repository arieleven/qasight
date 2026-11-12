"""A version's scorecard: tool scores rolled up by category, compared with the version before.

The overall score is the mean of the category scores, and a category's score
is the mean of its switched-on tools that have a result, so a category with ten
linters weighs the same as one with a single load test. Tools that are switched
off are still shown but don't count. The previous version is the one before it: by version number when
every version is one (``1.10`` after ``1.9``), else the one first scored before it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from qasight.services.project_health import grade_for
from qasight.services.scorecard.store import ScorecardStore, StoredResult
from qasight.services.scorecard.tools import CATEGORIES, TOOLS, TOOLS_BY_ID


@dataclass(frozen=True)
class ToolScore:
    tool: str
    name: str
    category: str
    enabled: bool
    score: int
    previous_score: int | None
    summary: str
    metrics: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    findings: list[dict[str, str]] = field(default_factory=list)
    recorded_at: float = 0.0
    source: str = "import"
    commit_sha: str | None = None


@dataclass(frozen=True)
class CategoryScore:
    key: str
    name: str
    score: int | None
    previous_score: int | None
    tools: list[str]


@dataclass(frozen=True)
class VersionSummary:
    version: str
    first_at: float
    last_at: float
    score: int | None
    grade: str | None
    tools: int


@dataclass(frozen=True)
class ProjectSummary:
    project: str
    versions: list[VersionSummary]


@dataclass(frozen=True)
class Scorecard:
    project: str
    version: str
    score: int | None
    grade: str | None
    previous_version: str | None
    previous_score: int | None
    recorded_at: float
    commit_sha: str | None
    categories: list[CategoryScore]
    tools: list[ToolScore]
    versions: list[VersionSummary]


def overall_score(
    results: Iterable[StoredResult], enabled: dict[str, bool]
) -> tuple[int | None, dict[str, int]]:
    """The overall score and each category's, from the switched-on tools' results."""
    by_category: dict[str, list[int]] = {}
    for result in results:
        tool = TOOLS_BY_ID.get(result.tool)
        if tool is None or not enabled.get(result.tool, tool.default_on):
            continue
        by_category.setdefault(tool.category, []).append(result.score)
    categories = {key: _half_up(sum(scores) / len(scores)) for key, scores in by_category.items()}
    if not categories:
        return None, {}
    return _half_up(sum(categories.values()) / len(categories)), categories


def _half_up(value: float) -> int:
    """Round .5 up, like the UI's Math.round (Python's round() goes to even)."""
    return math.floor(value + 0.5)


def _versions(results: list[StoredResult], enabled: dict[str, bool]) -> list[VersionSummary]:
    grouped: dict[str, list[StoredResult]] = {}
    for result in results:
        grouped.setdefault(result.version, []).append(result)
    out = []
    for version, rows in grouped.items():
        score, _ = overall_score(rows, enabled)
        out.append(
            VersionSummary(
                version=version,
                first_at=min(r.recorded_at for r in rows),
                last_at=max(r.recorded_at for r in rows),
                score=score,
                grade=grade_for(score),
                tools=len(rows),
            )
        )
    return sort_versions(out)


def _parsed(version: str) -> object | None:
    from packaging.version import InvalidVersion, Version

    try:
        return Version(version.strip().removeprefix("v").removeprefix("V"))
    except InvalidVersion:
        return None


def sort_versions(versions: list[VersionSummary]) -> list[VersionSummary]:
    """Newest first: by version number when every version is one (1.10 after 1.9), else by when it was first scored."""
    parsed = [_parsed(v.version) for v in versions]
    if versions and all(p is not None for p in parsed):
        pairs = sorted(zip(parsed, versions, strict=True), key=lambda pv: pv[0], reverse=True)  # type: ignore[arg-type,return-value]
        return [v for _p, v in pairs]
    return sorted(versions, key=lambda v: v.first_at, reverse=True)


def projects(store: ScorecardStore | None = None) -> list[ProjectSummary]:
    """Every scored project, the most recently scored first, each with its versions (newest first)."""
    store = store or ScorecardStore()
    enabled = store.enabled()
    grouped: dict[str, list[StoredResult]] = {}
    for result in store.results():
        grouped.setdefault(result.project, []).append(result)
    summaries = [
        ProjectSummary(project=p, versions=_versions(rows, enabled)) for p, rows in grouped.items()
    ]
    return sorted(summaries, key=lambda s: max(v.last_at for v in s.versions), reverse=True)


def build_scorecard(
    project: str | None = None,
    version: str | None = None,
    *,
    store: ScorecardStore | None = None,
) -> Scorecard | None:
    """The scorecard of ``project`` at ``version`` (default: the latest scored), or None."""
    store = store or ScorecardStore()
    enabled = store.enabled()
    if not project:
        known = projects(store)
        if not known:
            return None
        project = known[0].project
    rows = store.results(project)
    if not rows:
        return None
    versions = _versions(rows, enabled)
    if not version:
        version = versions[0].version
    names = [v.version for v in versions]
    if version not in names:
        return None
    index = names.index(version)
    previous = names[index + 1] if index + 1 < len(names) else None

    current = [r for r in rows if r.version == version]
    before = {r.tool: r for r in rows if r.version == previous} if previous else {}
    score, cat_scores = overall_score(current, enabled)
    prev_score, prev_cats = overall_score(before.values(), enabled) if previous else (None, {})

    order = {tool.id: i for i, tool in enumerate(TOOLS)}
    tools = [
        ToolScore(
            tool=r.tool,
            name=TOOLS_BY_ID[r.tool].name,
            category=TOOLS_BY_ID[r.tool].category,
            enabled=enabled.get(r.tool, True),
            score=r.score,
            previous_score=before[r.tool].score if r.tool in before else None,
            summary=r.summary,
            metrics=r.metrics,
            counts=r.counts,
            findings=r.findings,
            recorded_at=r.recorded_at,
            source=r.source,
            commit_sha=r.commit_sha,
        )
        for r in sorted(current, key=lambda r: order.get(r.tool, 999))
        if r.tool in TOOLS_BY_ID
    ]
    categories = [
        CategoryScore(
            key=key,
            name=name,
            score=cat_scores.get(key),
            previous_score=prev_cats.get(key),
            tools=[t.tool for t in tools if t.category == key],
        )
        for key, name in CATEGORIES.items()
        if any(t.category == key for t in tools)
    ]
    commits = [r.commit_sha for r in current if r.commit_sha]
    return Scorecard(
        project=project,
        version=version,
        score=score,
        grade=grade_for(score),
        previous_version=previous,
        previous_score=prev_score,
        recorded_at=max(r.recorded_at for r in current),
        commit_sha=commits[-1] if commits else None,
        categories=categories,
        tools=tools,
        versions=versions,
    )


def gate_failures(
    card: Scorecard,
    *,
    min_score: int | None = None,
    min_tool: int | None = None,
    no_drop: bool = False,
) -> list[str]:
    """Why ``card`` fails the release gate, one sentence each; empty when it passes."""
    failures: list[str] = []
    if min_score is not None and (card.score is None or card.score < min_score):
        failures.append(
            f"overall score {card.score if card.score is not None else 'none'} is below {min_score}"
        )
    for tool in card.tools:
        if not tool.enabled:
            continue
        if min_tool is not None and tool.score < min_tool:
            failures.append(f"{tool.name} scored {tool.score}, below {min_tool}")
        if no_drop and tool.previous_score is not None and tool.score < tool.previous_score:
            failures.append(
                f"{tool.name} dropped from {tool.previous_score} to {tool.score} since {card.previous_version}"
            )
    return failures


def _delta(score: int | None, previous: int | None) -> str:
    if score is None or previous is None:
        return ""
    diff = score - previous
    return " (=)" if diff == 0 else f" ({'+' if diff > 0 else ''}{diff})"


def to_markdown(card: Scorecard, *, skipped: dict[str, str] | None = None) -> str:
    """The scorecard as a Markdown document (a CI job summary, a release note, a PR comment)."""
    score = f"{card.score}/100 ({card.grade})" if card.score is not None else "no score"
    lines = [f"# Release scorecard: {card.project} {card.version}", ""]
    vs = (
        f", {_delta(card.score, card.previous_score).strip(' ()')} since {card.previous_version}"
        if card.previous_version and card.previous_score is not None
        else ""
    )
    lines.append(f"**Overall: {score}**{vs}.")
    if card.commit_sha:
        lines.append(f"Commit `{card.commit_sha[:12]}`.")
    lines += ["", "| Category | Score | Tools |", "|---|---:|---|"]
    names = {t.tool: t.name for t in card.tools}
    for cat in card.categories:
        value = (
            "off" if cat.score is None else f"{cat.score}{_delta(cat.score, cat.previous_score)}"
        )
        lines.append(f"| {cat.name} | {value} | {', '.join(names[t] for t in cat.tools)} |")
    lines += ["", "## Tools", "", "| Tool | Score | Result |", "|---|---:|---|"]
    for tool in card.tools:
        value = f"{tool.score}{_delta(tool.score, tool.previous_score)}" + (
            "" if tool.enabled else " (off)"
        )
        lines.append(f"| {tool.name} | {value} | {tool.summary.replace('|', '/')} |")
    worst = [
        (t, f)
        for t in card.tools
        if t.enabled
        for f in t.findings
        if f.get("severity") in ("critical", "high")
    ]
    if worst:
        lines += ["", "## Fix first", ""]
        for tool, finding in worst[:15]:
            where = f" `{finding['location']}`" if finding.get("location") else ""
            lines.append(
                f"- **{finding['severity']}** · {tool.name}: {finding['title']}{where}. {finding.get('detail', '')}".rstrip()
            )
    if skipped:
        lines += ["", "## Not run", ""]
        for tool_id, why in skipped.items():
            lines.append(f"- {TOOLS_BY_ID[tool_id].name}: {why}")
    lines += [
        "",
        "_Made by `qasight scorecard`. Scores are computed from the tools' own reports, no AI._",
        "",
    ]
    return "\n".join(lines)
