"""A version's scorecard: tool scores rolled up by category, compared with the version before.

The overall score is the mean of the category scores; a category with nothing
measured is left out, not counted as zero. Inside a category, the findings of
all its switched-on scanners and linters are rated together the way SonarQube
and Codacy rate a codebase (:mod:`qasight.services.scorecard.rating`): security
and dependencies by the worst finding, code quality by issues per 1,000 source
lines. Tools that measure something else (a Lighthouse score, coverage, a load
test) keep their own 0-100 and are averaged in. Tools that are switched
off are still shown but don't count. A scan also keeps its **coverage**: the
tools that fit the repository and which of them ran. When fewer than half ran,
the grade is capped at C, so a 98 from one linter doesn't read as an A. The previous version is the one before it: by version number when
every version is one (``1.10`` after ``1.9``), else the one first scored before it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from qasight.services.project_health import grade_for
from qasight.services.scorecard.megalinter import name_from_id
from qasight.services.scorecard.rating import findings_score
from qasight.services.scorecard.store import (
    ScorecardStore,
    StoredCoverage,
    StoredResult,
    is_engine_tool,
)
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
    # Findings not in the version before (by what they are and which file), when known.
    new_counts: dict[str, int] | None = None


@dataclass(frozen=True)
class CategoryScore:
    key: str
    name: str
    score: int | None
    previous_score: int | None
    tools: list[str]


@dataclass(frozen=True)
class Coverage:
    """How many of the tools that fit the repository gave a result, and why the others didn't."""

    ran: int
    expected: int
    missing: dict[str, str] = field(default_factory=dict)
    # The grade the score alone would get, when coverage capped it.
    capped_from: str | None = None

    @property
    def share(self) -> float:
        return self.ran / self.expected if self.expected else 1.0


# Under this share of the fitting tools, the grade is capped at GRADE_CAP.
MIN_COVERAGE = 0.5
GRADE_CAP = "C"
_GRADE_ORDER = "ABCDF"


def _with_engine(tools: set[str]) -> set[str]:
    """The tools with a result, counting the engine as one when any linter it ran has one."""
    return tools | ({"megalinter"} if any(is_engine_tool(t) for t in tools) else set())


def coverage_of(stored: StoredCoverage | None, tools_with_results: set[str]) -> Coverage | None:
    if stored is None or not stored.expected:
        return None
    ran = [t for t in stored.expected if t in tools_with_results]
    missing = {
        t: stored.missing.get(t, "no result")
        for t in stored.expected
        if t not in tools_with_results
    }
    return Coverage(ran=len(ran), expected=len(stored.expected), missing=missing)


def capped_grade(score: int | None, coverage: Coverage | None) -> tuple[str | None, str | None]:
    """(grade, the uncapped grade when coverage capped it, else None)."""
    grade = grade_for(score)
    if grade is None or coverage is None or coverage.share >= MIN_COVERAGE:
        return grade, None
    if _GRADE_ORDER.index(grade) < _GRADE_ORDER.index(GRADE_CAP):
        return GRADE_CAP, grade
    return grade, None


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
    coverage: Coverage | None = None
    source_lines: int | None = None


def category_of(result: StoredResult) -> str | None:
    tool = TOOLS_BY_ID.get(result.tool)
    return tool.category if tool is not None else result.category


def name_of(result: StoredResult) -> str:
    tool = TOOLS_BY_ID.get(result.tool)
    return tool.name if tool is not None else (result.name or result.tool)


def _is_on(result: StoredResult, enabled: dict[str, bool]) -> bool:
    tool = TOOLS_BY_ID.get(result.tool)
    if tool is not None:
        return enabled.get(result.tool, tool.default_on)
    return is_engine_tool(result.tool) and enabled.get(result.tool, True)


def source_lines(results: Iterable[StoredResult]) -> int | None:
    """The repository's source lines, as the scan that made ``results`` counted them."""
    values = [int(r.metrics.get("source_lines") or 0) for r in results]
    best = max(values, default=0)
    return best or None


def _is_findings(result: StoredResult) -> bool:
    return "findings" in result.metrics


def is_advisory(result: StoredResult) -> bool:
    """A linter shown but not scored: it repeats the project's own choice on default rules."""
    return bool(result.metrics.get("advisory"))


def rated_score(result: StoredResult, lines: int | None) -> int:
    """A tool's score: its findings rated against the repository's size, else its own score."""
    category = category_of(result)
    if lines and category and _is_findings(result):
        return findings_score(category, result.counts, lines)
    return result.score


def overall_score(
    results: Iterable[StoredResult], enabled: dict[str, bool]
) -> tuple[int | None, dict[str, int]]:
    """The overall score and each category's, from the switched-on tools' results."""
    rows = [r for r in results if _is_on(r, enabled) and category_of(r) and not is_advisory(r)]
    lines = source_lines(rows)
    by_category: dict[str, list[int]] = {}
    counts: dict[str, dict[str, int]] = {}
    for result in rows:
        category = category_of(result)
        assert category is not None
        if lines and _is_findings(result):
            total = counts.setdefault(category, {})
            for sev, n in result.counts.items():
                total[sev] = total.get(sev, 0) + int(n)
        else:
            by_category.setdefault(category, []).append(result.score)
    for category, total in counts.items():
        assert lines is not None
        by_category.setdefault(category, []).append(findings_score(category, total, lines))
    categories = {key: _half_up(sum(scores) / len(scores)) for key, scores in by_category.items()}
    if not categories:
        return None, {}
    return _half_up(sum(categories.values()) / len(categories)), categories


def new_counts(current: StoredResult, before: StoredResult | None) -> dict[str, int] | None:
    """Findings of ``current`` not in ``before``, by severity; None when it can't be told."""
    if before is None:
        return None
    if not before.fingerprints and any(before.counts.values()):
        return None  # scored before fingerprints were kept
    if not current.fingerprints and any(current.counts.values()):
        return None
    left: dict[str, int] = {}
    for fp in before.fingerprints:
        left[fp] = left.get(fp, 0) + 1
    out: dict[str, int] = {}
    for fp in current.fingerprints:
        if left.get(fp):
            left[fp] -= 1
            continue
        severity = fp.split(":", 1)[0]
        out[severity] = out.get(severity, 0) + 1
    return out


def _half_up(value: float) -> int:
    """Round .5 up, like the UI's Math.round (Python's round() goes to even)."""
    return math.floor(value + 0.5)


def _versions(
    results: list[StoredResult],
    enabled: dict[str, bool],
    coverages: dict[str, StoredCoverage] | None = None,
) -> list[VersionSummary]:
    grouped: dict[str, list[StoredResult]] = {}
    for result in results:
        grouped.setdefault(result.version, []).append(result)
    out = []
    for version, rows in grouped.items():
        score, _ = overall_score(rows, enabled)
        cov = coverage_of((coverages or {}).get(version), _with_engine({r.tool for r in rows}))
        out.append(
            VersionSummary(
                version=version,
                first_at=min(r.recorded_at for r in rows),
                last_at=max(r.recorded_at for r in rows),
                score=score,
                grade=capped_grade(score, cov)[0],
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
    coverages: dict[str, dict[str, StoredCoverage]] = {}
    for key, cov in store.coverages().items():
        project, _, version = key.partition("\n")
        coverages.setdefault(project, {})[version] = cov
    summaries = [
        ProjectSummary(project=p, versions=_versions(rows, enabled, coverages.get(p)))
        for p, rows in grouped.items()
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
    coverages = store.coverages(project)
    versions = _versions(rows, enabled, coverages)
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
    lines = source_lines(current)
    prev_lines = source_lines(before.values())
    tools = [
        ToolScore(
            tool=r.tool,
            name=name_of(r),
            category=category_of(r) or "code",
            enabled=_is_on(r, enabled),
            score=rated_score(r, lines),
            previous_score=rated_score(before[r.tool], prev_lines) if r.tool in before else None,
            summary=r.summary,
            metrics=r.metrics,
            counts=r.counts,
            findings=r.findings,
            recorded_at=r.recorded_at,
            source=r.source,
            commit_sha=r.commit_sha,
            new_counts=new_counts(r, before.get(r.tool)),
        )
        for r in sorted(
            current,
            key=lambda r: (order.get(r.tool.split(":", 1)[0], 999), name_of(r).lower()),
        )
        if r.tool in TOOLS_BY_ID or is_engine_tool(r.tool)
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
    coverage = coverage_of(coverages.get(version), _with_engine({r.tool for r in current}))
    grade, uncapped = capped_grade(score, coverage)
    if coverage is not None and uncapped is not None:
        coverage = Coverage(
            ran=coverage.ran,
            expected=coverage.expected,
            missing=coverage.missing,
            capped_from=uncapped,
        )
    return Scorecard(
        project=project,
        version=version,
        score=score,
        grade=grade,
        previous_version=previous,
        previous_score=prev_score,
        recorded_at=max(r.recorded_at for r in current),
        commit_sha=commits[-1] if commits else None,
        categories=categories,
        tools=tools,
        versions=versions,
        coverage=coverage,
        source_lines=lines,
    )


def gate_failures(
    card: Scorecard,
    *,
    min_score: int | None = None,
    min_tool: int | None = None,
    no_drop: bool = False,
    no_new: bool = False,
) -> list[str]:
    """Why ``card`` fails the release gate, one sentence each; empty when it passes.

    ``no_new`` fails on any new high or critical finding since the version before
    (SonarQube's "new code" and Trunk's "hold the line": old debt doesn't block a release).
    """
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
        if no_new and tool.new_counts:
            serious = {s: n for s, n in tool.new_counts.items() if s in ("critical", "high") and n}
            if serious:
                what = ", ".join(f"{n} {s}" for s, n in serious.items())
                failures.append(
                    f"{tool.name} has new findings since {card.previous_version}: {what}"
                )
    return failures


def coverage_sentence(coverage: Coverage) -> str:
    """``5 of 8 tools that fit this repository ran.`` plus the cap, when there is one."""
    text = f"{coverage.ran} of {coverage.expected} tools that fit this repository ran."
    if coverage.capped_from:
        text += (
            f" The grade is capped at {GRADE_CAP} (the score alone is a {coverage.capped_from})"
            f" because fewer than half of them ran."
        )
    return text


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
    if card.coverage is not None:
        lines.append(coverage_sentence(card.coverage))
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
    not_run = {**(card.coverage.missing if card.coverage else {}), **(skipped or {})}
    if not_run:
        lines += ["", "## Not run", ""]
        for tool_id, why in not_run.items():
            known = TOOLS_BY_ID.get(tool_id)
            lines.append(f"- {known.name if known else name_from_id(tool_id)}: {why}")
    lines += [
        "",
        "_Made by `qasight scorecard`. Scores are computed from the tools' own reports, no AI._",
        "",
    ]
    return "\n".join(lines)
