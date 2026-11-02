"""The failing tests' own source, for the AI failure summary when the person allows it.

Off by default (``share_test_source`` in the AI settings). When on, the summary's
prompt gets a short, line-numbered excerpt of each failing test: the test's own
function (found by its name) and a few lines around the stack-trace frames that
point into the same file. Never whole files and never the rest of the repository.

Limits, all enforced here:

- only files inside the run's checkout (``target_repo``, else the cycle's latest
  recorded checkout), after resolving symlinks, so ``../`` and absolute paths in a
  trace can't reach anything else;
- files that look like secrets (``.env*``, keys, certificates, credential stores)
  and anything under ``.git``, ``.ssh`` or similar are skipped, as are files over
  :attr:`SourceBudget.max_file_bytes`, binary files and files holding a private key;
- at most :attr:`SourceBudget.max_files` files, each excerpt capped, the whole
  block capped, and everything passed through the same redaction as failure logs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from testo_core import paths
from testo_core.history.views import CompletedRunView
from testo_core.reporting.allure_cases import case_group
from testo_core.security.redaction import redact_text
from testo_core.services.test_ownership import source_path

_FAILED = frozenset({"failed", "broken"})
_FRAMES = (
    re.compile(r"^(?P<file>[^\s:\"'][^:\n\"']*\.\w+):(?P<line>\d+):", re.MULTILINE),
    re.compile(r"File \"(?P<file>[^\"\n]+)\", line (?P<line>\d+)"),
)

# File names (lower-cased) that are never read, whatever the test says.
_SECRET_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".pgpass",
        ".htpasswd",
        ".git-credentials",
        ".dockercfg",
        "credentials",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
_SECRET_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".kdbx",
    ".gpg",
    ".asc",
    ".crt",
    ".cer",
    ".der",
    ".tfstate",
    ".tfvars",
)
_SECRET_WORDS = re.compile(r"secret|credential|password|passwd|token|private[-_]?key", re.I)
_SECRET_DIRS = frozenset({".git", ".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker"})
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


@dataclass(frozen=True)
class SourceBudget:
    max_files: int = 3
    max_file_bytes: int = 512_000
    max_snippet_chars: int = 2_000
    max_total_chars: int = 4_000
    # Lines shown before and after a stack-trace frame.
    context_lines: int = 8
    # Longest test function shown whole; longer ones are cut there.
    max_function_lines: int = 60


@dataclass(frozen=True)
class SourceSnippet:
    path: str
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True)
class FailingSource:
    """One failing test as far as finding its source goes."""

    name: str
    full_name: str = ""
    group: str = ""
    trace: str = ""


def is_secret_looking(rel_path: str) -> bool:
    """True for paths never sent to a model: env files, keys, credential stores, VCS dirs."""
    parts = PurePath(rel_path).parts
    if any(part.lower() in _SECRET_DIRS for part in parts[:-1]):
        return True
    name = parts[-1].lower() if parts else ""
    if not name:
        return True
    if name.startswith(".env") or name.endswith(".env") or name in _SECRET_NAMES:
        return True
    if name.endswith(_SECRET_SUFFIXES):
        return True
    return bool(_SECRET_WORDS.search(name))


def _frames(trace: str) -> list[tuple[str, int]]:
    found = [(m.start(), m["file"], int(m["line"])) for rx in _FRAMES for m in rx.finditer(trace)]
    return [(file, line) for _, file, line in sorted(found)]


def _inside(repo: Path, candidate: str) -> tuple[str, Path] | None:
    """``(repo-relative path, resolved file)`` when ``candidate`` is a file inside ``repo``."""
    raw = Path(candidate)
    try:
        resolved = (raw if raw.is_absolute() else repo / raw).resolve()
        rel = resolved.relative_to(repo).as_posix()
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return rel, resolved


def _read_text(path: Path, budget: SourceBudget) -> list[str] | None:
    try:
        if path.stat().st_size > budget.max_file_bytes:
            return None
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if "\x00" in text or _PRIVATE_KEY.search(text):
        return None
    return text.splitlines()


def _bare_name(name: str) -> str:
    """``test_x`` from ``test_x[param]`` or ``test_x()``; the last part of a dotted name."""
    name = re.sub(r"[\[(].*$", "", name.strip()).strip()
    return name.rsplit(".", 1)[-1].rsplit("::", 1)[-1].strip()


def _definition_line(lines: list[str], name: str) -> int | None:
    """0-based line where the test called ``name`` is defined, if it can be told."""
    bare = _bare_name(name)
    if not bare:
        return None
    word = re.escape(bare)
    patterns = [
        re.compile(rf"^\s*(async\s+)?def\s+{word}\b"),  # Python
        re.compile(rf"^\s*func\s+(\([^)]*\)\s*)?{word}\b"),  # Go
        re.compile(rf"^\s*(Scenario|Scenario Outline):\s*{re.escape(name.strip())}\s*$"),
        re.compile(rf"""\b(it|test|describe|specify)\s*\(\s*['"`]{re.escape(name.strip())}"""),
        re.compile(rf"\b{word}\s*\("),  # Java, C#, Kotlin, Swift methods
    ]
    for rx in patterns:
        for index, line in enumerate(lines):
            if rx.search(line):
                return index
    return None


def _block_end(lines: list[str], start: int, budget: SourceBudget) -> int:
    """Last 0-based line of the definition starting at ``start``."""
    limit = min(len(lines) - 1, start + budget.max_function_lines - 1)
    first = lines[start]
    indent = len(first) - len(first.lstrip())
    if first.lstrip().startswith(("def ", "async def ", "Scenario")):
        end = start
        for index in range(start + 1, limit + 1):
            line = lines[index]
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            if line.strip():
                end = index
        return end
    depth, opened = 0, False
    for index in range(start, limit + 1):
        depth += lines[index].count("{") - lines[index].count("}")
        opened = opened or "{" in lines[index]
        if opened and depth <= 0:
            return index
    return limit


def _render(lines: list[str], start: int, end: int) -> str:
    width = len(str(end + 1))
    return "\n".join(f"{n + 1:>{width}} | {lines[n]}" for n in range(start, end + 1))


def _snippets_for_file(
    rel: str, lines: list[str], *, name: str, frame_lines: Iterable[int], budget: SourceBudget
) -> list[SourceSnippet]:
    ranges: list[tuple[int, int]] = []
    start = _definition_line(lines, name)
    if start is not None:
        ranges.append((start, _block_end(lines, start, budget)))
    for line in frame_lines:
        index = line - 1
        if 0 <= index < len(lines):
            ranges.append(
                (
                    max(0, index - budget.context_lines),
                    min(len(lines) - 1, index + budget.context_lines),
                )
            )
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    out = []
    for lo, hi in merged:
        text = redact_text(_render(lines, lo, hi))
        if len(text) > budget.max_snippet_chars:
            text = text[: budget.max_snippet_chars].rsplit("\n", 1)[0] + "\n...[cut]"
        out.append(SourceSnippet(path=rel, start_line=lo + 1, end_line=hi + 1, text=text))
    return out


def collect_sources(
    tests: Iterable[FailingSource], *, repo: Path | None, budget: SourceBudget | None = None
) -> list[SourceSnippet]:
    """Excerpts of the failing tests' source inside ``repo``, within ``budget``."""
    budget = budget or SourceBudget()
    if repo is None:
        return []
    try:
        repo = repo.resolve()
    except OSError:
        return []
    if not repo.is_dir():
        return []
    snippets: list[SourceSnippet] = []
    files: set[str] = set()
    total = 0
    for test in tests:
        candidates: dict[str, tuple[Path, list[int]]] = {}
        guessed = source_path(test.full_name, test.group)
        for raw, line in [*((f, n) for f, n in _frames(test.trace)), (guessed, 0)]:
            if not raw:
                continue
            found = _inside(repo, raw)
            if found is None or is_secret_looking(found[0]):
                continue
            rel, resolved = found
            entry = candidates.setdefault(rel, (resolved, []))
            if line:
                entry[1].append(line)
        for rel, (resolved, frame_lines) in candidates.items():
            if rel not in files and len(files) >= budget.max_files:
                continue
            lines = _read_text(resolved, budget)
            if lines is None:
                continue
            found_any = False
            for snippet in _snippets_for_file(
                rel, lines, name=test.name, frame_lines=frame_lines, budget=budget
            ):
                if any(
                    s.path == snippet.path and s.start_line == snippet.start_line for s in snippets
                ):
                    continue
                if total + len(snippet.text) > budget.max_total_chars:
                    return snippets
                snippets.append(snippet)
                total += len(snippet.text)
                found_any = True
            if found_any:
                files.add(rel)
    return snippets


def format_sources(snippets: Iterable[SourceSnippet]) -> str:
    return "\n\n".join(
        f"--- {s.path} (lines {s.start_line}-{s.end_line})\n{s.text}" for s in snippets
    )


def failing_tests_of(run: CompletedRunView) -> list[FailingSource]:
    """The run's failing tests with their stack traces, from its Allure results."""
    out: list[FailingSource] = []
    if run.snapshot_dir:
        root = paths.ORCHESTRATOR_ROOT / run.snapshot_dir
        for path in sorted(root.rglob("*-result.json")) if root.is_dir() else []:
            try:
                data: Any = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            if str(data.get("status") or "").lower() not in _FAILED:
                continue
            details = data.get("statusDetails") or {}
            details = details if isinstance(details, dict) else {}
            out.append(
                FailingSource(
                    name=str(data.get("name") or data.get("fullName") or ""),
                    full_name=str(data.get("fullName") or ""),
                    group=case_group(data),
                    trace=f"{details.get('message') or ''}\n{details.get('trace') or ''}",
                )
            )
    if out:
        return out
    from testo_core.history.cases import cases_for_run

    for case in cases_for_run(run).values():
        if case.get("status") in _FAILED:
            out.append(
                FailingSource(
                    name=str(case.get("name") or ""),
                    full_name=str(case.get("full_name") or ""),
                    group=str(case.get("group") or ""),
                    trace=str(case.get("message") or ""),
                )
            )
    return sorted(out, key=lambda t: t.name)


def checkout_of(run: CompletedRunView) -> Path | None:
    """Where the run's tests live: its ``target_repo``, else the cycle's latest recorded one."""
    if run.target_repo:
        return Path(run.target_repo)
    if run.cycle:
        from testo_core.services.test_history import cycle_source

        return cycle_source(run.cycle)[0]
    return None
