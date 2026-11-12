"""What a repository is made of, for any language: its languages, its source lines, and what each path is.

The scorecard's scores are per 1,000 lines of source code, so a big repository
isn't punished for being big. Only **source** paths count: tests, vendored and
generated code and docs are shown but don't change the score (the review of
CIViC and cBioPortal showed secrets scanners flagging test fixtures as leaks).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

PathClass = Literal["source", "test", "vendor", "generated", "docs"]

# File extension -> language, for counting lines and naming the stack.
LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".groovy": "groovy",
    ".rb": "ruby",
    ".erb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".vb": "vbdotnet",
    ".fs": "fsharp",
    ".php": "php",
    ".swift": "swift",
    ".m": "objective-c",
    ".dart": "dart",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".r": "r",
    ".R": "r",
    ".sh": "bash",
    ".bash": "bash",
    ".ps1": "powershell",
    ".sql": "sql",
    ".tf": "terraform",
    ".clj": "clojure",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".less": "css",
}

# Folders never walked: version control, dependencies, build output, caches.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "bower_components",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".gradle",
        ".idea",
        ".vscode",
        "target",
        "megalinter-reports",
    }
)

_TEST_DIR = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|testing|test[-_]?data|testdata|fixtures?|__mocks__|e2e|cypress|features)(/|$)",
    re.I,
)
_TEST_FILE = re.compile(
    r"(^|/)(test_[^/]*|[^/]*_test\.[^/]+|[^/]*[._-](spec|test)s?\.[^/]+|[^/]*Tests?\.(java|kt|cs|scala|groovy|swift))$",
    re.I,
)
_VENDOR = re.compile(
    r"(^|/)(vendor|vendors|third[-_]?party|external|extern|deps|node_modules|bower_components)(/|$)",
    re.I,
)
_GENERATED = re.compile(
    r"(^|/)(dist|build|out|generated|gen|\.next|coverage|site-packages)(/|$)"
    r"|\.min\.(js|css)$|_pb2(_grpc)?\.py$|\.pb\.go$|\.g\.dart$|\.designer\.cs$|(^|/)migrations?/[^/]+\.py$",
    re.I,
)
# Lock files and manifests are where dependency findings point: always counted.
_MANIFEST = re.compile(
    r"(^|/)(package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|requirements[^/]*\.txt|pyproject\.toml|"
    r"poetry\.lock|uv\.lock|Pipfile(\.lock)?|Gemfile(\.lock)?|Cargo\.(toml|lock)|go\.(mod|sum)|pom\.xml|"
    r"build\.gradle(\.kts)?|composer\.(json|lock)|[^/]*\.csproj|packages\.lock\.json)$",
    re.I,
)
_DOCS = re.compile(r"(^|/)(docs?|documentation|examples?|samples?)(/|$)|\.(md|rst|adoc)$", re.I)


def lint_exclude_regex() -> str:
    """One regex (Python syntax, as MegaLinter reads it) for the paths that aren't source.

    Given to MegaLinter as ``FILTER_REGEX_EXCLUDE`` so its linters skip tests, vendored and
    generated code and docs, the same paths the score leaves out (QASight's own run had 10,000
    stylelint errors in Obsidian themes under ``docs/``).
    """
    parts = (_VENDOR, _GENERATED, _TEST_DIR, _TEST_FILE, _DOCS)
    return "(?i)" + "|".join(f"(?:{p.pattern})" for p in parts)


# Files counted when the scan walks a big repository (the Linux kernel has ~80,000).
WALK_LIMIT = 200_000


def classify(path: str) -> PathClass:
    """What ``path`` (relative, ``/``-separated or a ``file://`` URI) is."""
    clean = path.strip().removeprefix("file://").replace("\\", "/")
    clean = re.sub(r"^(/tmp/lint|/github/workspace)/", "", clean)
    while clean.startswith(("./", "/")):
        clean = clean[1:] if clean.startswith("/") else clean[2:]
    clean = clean.split(":", 1)[0] if re.search(r":\d+$", clean) else clean
    if _MANIFEST.search(clean) and not _VENDOR.search(clean):
        return "source"
    if _VENDOR.search(clean):
        return "vendor"
    if _GENERATED.search(clean):
        return "generated"
    if _TEST_DIR.search(clean) or _TEST_FILE.search(clean):
        return "test"
    if _DOCS.search(clean):
        return "docs"
    return "source"


@dataclass
class RepoProfile:
    """The languages of a repository by source lines, and its total source lines."""

    lines: dict[str, int] = field(default_factory=dict)
    files: int = 0
    truncated: bool = False

    @property
    def source_lines(self) -> int:
        return sum(self.lines.values())

    @property
    def languages(self) -> list[str]:
        """Languages, the most lines first."""
        return [lang for lang, _n in sorted(self.lines.items(), key=lambda kv: -kv[1])]

    def main_language(self, share: float = 0.6) -> str | None:
        """The language with at least ``share`` of the source lines, if one has."""
        total = self.source_lines
        if not total:
            return None
        lang, n = max(self.lines.items(), key=lambda kv: kv[1])
        return lang if n / total >= share else None


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def profile(repo: Path, *, limit: int = WALK_LIMIT) -> RepoProfile:
    """Walk ``repo`` and count the non-blank lines of its source files, by language."""
    out = RepoProfile()
    root = str(repo)
    for current, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(current, root).replace(os.sep, "/")
        for name in files:
            suffix = os.path.splitext(name)[1]
            lang = LANGUAGES.get(suffix) or LANGUAGES.get(suffix.lower())
            if lang is None:
                continue
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if classify(rel) != "source":
                continue
            out.files += 1
            out.lines[lang] = out.lines.get(lang, 0) + _count_lines(Path(current) / name)
            if out.files >= limit:
                out.truncated = True
                return out
    return out


def source_only(locations: Iterable[str]) -> list[bool]:
    """For each location, whether it is in source code (an empty location counts as source)."""
    return [not loc or classify(loc) == "source" for loc in locations]
