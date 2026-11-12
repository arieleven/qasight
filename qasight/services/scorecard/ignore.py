"""Accepted findings: a project's ignore file drops known false positives before the score is made.

The file is YAML, ``scorecard-ignore.yml`` at the repository's root (or the
file given with ``--ignore``)::

    ignore:
      - tool: zizmor
        rule: dangerous-triggers
        path: .github/workflows/release-management.yml
        reason: pull_request_target that checks nothing out
      - tool: bandit
        rule: "B101*"          # globs work in rule and path
        path: "scripts/**"

Each entry names a ``tool`` and at least one of ``rule`` (the finding's title:
a rule id, a package) and ``path`` (the file in its location). A finding that
matches an entry is left out of the score and counted as ignored; the reason
is for people reading the file. Only findings tools (Bandit, Semgrep, gitleaks,
audits, ...) can be ignored this way; a ratio tool's score is its own.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from qasight.services.scorecard.parsers import Finding, ToolResult

IGNORE_FILE = "scorecard-ignore.yml"


class IgnoreFileError(ValueError):
    """The ignore file isn't valid YAML in the expected shape."""


@dataclass(frozen=True)
class IgnoreRule:
    tool: str
    rule: str | None = None
    path: str | None = None
    reason: str = ""

    def matches(self, tool: str, finding: Finding) -> bool:
        if tool != self.tool:
            return False
        if self.rule is not None and not fnmatchcase(finding.title, self.rule):
            return False
        if self.path is not None:
            where = (
                finding.location.rsplit(":", 1)[0] if ":" in finding.location else finding.location
            )
            where = where.removeprefix("./")
            if not (
                fnmatchcase(where, self.path) or fnmatchcase(where, self.path.rstrip("/") + "/*")
            ):
                return False
        return True


def load_ignores(path: Path) -> list[IgnoreRule]:
    """The rules in ``path``; raises :class:`IgnoreFileError` for a bad file."""
    import yaml

    from qasight.services.scorecard.tools import TOOLS_BY_ID

    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise IgnoreFileError(f"can't read {path}: {exc}") from exc
    entries = data.get("ignore") if isinstance(data, dict) else None
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise IgnoreFileError(f"{path}: `ignore` is a list of entries")
    rules: list[IgnoreRule] = []
    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise IgnoreFileError(f"{path}: entry {i} is not a mapping")
        tool = str(entry.get("tool") or "").strip()
        if tool not in TOOLS_BY_ID:
            raise IgnoreFileError(f"{path}: entry {i} names unknown tool {tool!r}")
        rule = entry.get("rule")
        where = entry.get("path")
        if rule is None and where is None:
            raise IgnoreFileError(f"{path}: entry {i} needs a rule, a path or both")
        rules.append(
            IgnoreRule(
                tool=tool,
                rule=None if rule is None else str(rule),
                path=None if where is None else str(where).removeprefix("./"),
                reason=str(entry.get("reason") or ""),
            )
        )
    return rules


def find_ignore_file(repo: Path) -> Path | None:
    candidate = repo / IGNORE_FILE
    return candidate if candidate.is_file() else None


def apply_ignores(tool: str, result: ToolResult, rules: list[IgnoreRule]) -> ToolResult:
    """``result`` without the findings ``rules`` accept, rescored; unchanged when none match."""
    mine = [r for r in rules if r.tool == tool]
    if not mine or result.rescore is None:
        return result
    kept = [f for f in result.all_findings if not any(r.matches(tool, f) for r in mine)]
    ignored = len(result.all_findings) - len(kept)
    if not ignored:
        return result
    rescored = result.rescore(kept)
    return replace(
        rescored,
        summary=f"{rescored.summary}; {ignored} ignored",
        metrics={**rescored.metrics, "ignored": ignored},
    )
