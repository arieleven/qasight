"""Pick the tests a change can affect, and say why each one was picked.

Used by ``qasight affected`` and ``pytest --qasight-affected``; off unless asked.

The changed files come from git (the working tree against the merge base with
the baseline branch, plus untracked files) or from a list. Each changed file is
mapped to tests by the first of these that applies (``affected:`` in
``qasight.yaml``, :mod:`qasight.config.affected`):

1. ``run_all_when`` globs: the file can change any test (``conftest.py``, a
   lockfile, ``qasight.yaml``), so everything runs;
2. ``ignore`` globs: the file can't change a test (docs), nothing runs for it;
3. the file is a test file itself: it runs (unless it was deleted);
4. ``rules``: ``changed`` globs to ``tests`` globs or pytest node ids;
5. a coverage.py JSON report with test contexts (``pytest --cov --cov-context=test``,
   then ``coverage json --show-contexts``): the tests that ran a line of the file.

A file none of these maps, a rule whose tests don't exist, a failed ``git diff``
or an empty diff each mean "run everything", with the reason. Too many tests
costs time; too few hides a failure, so when unsure it runs all.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from qasight.config.affected import AffectedConfig

_GIT_TIMEOUT_S = 60.0


class AffectedError(ValueError):
    """A coverage report or git command that failed."""


@dataclass(frozen=True)
class FileDecision:
    path: str
    # "run_all", "ignored", "test_file", "deleted_test", "rule", "coverage" or "unmapped"
    outcome: str
    reason: str
    tests: tuple[str, ...] = ()


@dataclass
class Selection:
    base: str | None
    changed: tuple[str, ...]
    files: list[FileDecision] = field(default_factory=list)
    # Test (repo-relative file or pytest node id) -> why it was picked.
    tests: dict[str, list[str]] = field(default_factory=dict)
    run_all_reasons: list[str] = field(default_factory=list)

    @property
    def run_all(self) -> bool:
        return bool(self.run_all_reasons)

    @property
    def nothing_to_run(self) -> bool:
        return not self.run_all and not self.tests

    def to_json(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "run_all": self.run_all,
            "run_all_reasons": list(self.run_all_reasons),
            "nothing_to_run": self.nothing_to_run,
            "changed": list(self.changed),
            "tests": [{"test": t, "reasons": list(r)} for t, r in self.tests.items()],
            "files": [
                {"path": f.path, "outcome": f.outcome, "reason": f.reason, "tests": list(f.tests)}
                for f in self.files
            ],
        }


# -- globs ------------------------------------------------------------------------


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """gitignore-style: ``**`` spans folders, ``*`` stays in one, a name with no ``/`` matches anywhere."""
    pattern = pattern.strip()
    if pattern.startswith("./"):
        pattern = pattern[2:]
    anchored = "/" in pattern.rstrip("/")
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile(("" if anchored else "(?:.*/)?") + "".join(out) + r"\Z")


def matches(path: str, patterns: Iterable[str]) -> str | None:
    """The first pattern ``path`` (repo-relative, ``/``-separated) matches, if any."""
    for pattern in patterns:
        if _glob_regex(pattern).match(path):
            return pattern
    return None


def _is_test_file(path: str, patterns: Sequence[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(
        bool(_glob_regex(p).match(path)) if "/" in p else fnmatch.fnmatchcase(name, p)
        for p in patterns
    )


# -- git --------------------------------------------------------------------------


def _git(argv: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv
            # quotePath=false: non-ASCII paths come out as themselves, not "tests/test_\\303\\251.py".
            ["git", "-c", "core.quotePath=false", *argv],  # noqa: S607 — git from PATH, like config/triggers.py
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AffectedError(f"git {argv[0]} failed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise AffectedError(detail[0] if detail else f"git {argv[0]} exited {proc.returncode}")
    return proc.stdout


def repo_root(start: Path) -> Path | None:
    try:
        return Path(_git(["rev-parse", "--show-toplevel"], start).strip()).resolve()
    except AffectedError:
        return None


def default_base(env: Mapping[str, str], configured: str | None, root: Path) -> str:
    """``--base`` or ``affected.base``, else the pull request's target branch, else ``origin/main``."""
    if configured:
        return configured
    from qasight.services.ci_provenance import detect_ci_provenance

    provenance = detect_ci_provenance(env)
    branch = provenance.ci_base_ref if provenance else None
    candidates = ([f"origin/{branch}", branch] if branch else []) + ["origin/main", "main"]
    for candidate in candidates:
        try:
            _git(["rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"], root)
            return candidate
        except AffectedError:
            continue
    return candidates[0]


def changed_files(root: Path, base: str) -> tuple[str, ...]:
    """Files changed since the merge base with ``base``: commits, uncommitted edits and new files.

    ``--no-renames`` lists both names of a moved file, so the old path's tests run too.
    """
    merge_base = _git(["merge-base", base, "HEAD"], root).strip()
    diff = _git(["diff", "--name-only", "--no-renames", merge_base], root)
    untracked = _git(["ls-files", "--others", "--exclude-standard"], root)
    seen: dict[str, None] = {}
    for line in (*diff.splitlines(), *untracked.splitlines()):
        if line.strip():
            seen.setdefault(line.strip(), None)
    return tuple(seen)


def repo_files(root: Path) -> tuple[str, ...]:
    """Every file git tracks or would add, to expand a rule's ``tests`` globs."""
    try:
        out = _git(["ls-files", "--cached", "--others", "--exclude-standard"], root)
        return tuple(line for line in out.splitlines() if line.strip())
    except AffectedError:
        return tuple(
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts
        )


# -- coverage ---------------------------------------------------------------------


def load_coverage_map(path: Path, root: Path) -> dict[str, frozenset[str]]:
    """Source file -> pytest node ids that ran a line of it, from ``coverage json --show-contexts``.

    Contexts look like ``tests/test_x.py::test_y|run`` (pytest-cov's ``--cov-context=test``).
    A file that only ran outside a test (at import) maps to no test, so a change to it runs all.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AffectedError(f"could not read coverage report {path}: {exc}") from exc
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise AffectedError(f"{path} is not a coverage.py JSON report (no 'files').")
    out: dict[str, frozenset[str]] = {}
    any_contexts = False
    for name, entry in files.items():
        contexts = entry.get("contexts") if isinstance(entry, dict) else None
        tests: set[str] = set()
        if isinstance(contexts, dict):
            any_contexts = True
            for names in contexts.values():
                for ctx in names or ():
                    test = str(ctx).split("|", 1)[0].strip()
                    if "::" in test:
                        tests.add(test)
        out[_relative(str(name), root)] = frozenset(tests)
    if files and not any_contexts:
        raise AffectedError(
            f"{path} has no test contexts: run `pytest --cov --cov-context=test`, "
            "then `coverage json --show-contexts`."
        )
    return out


def _relative(name: str, root: Path) -> str:
    p = Path(name)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root).as_posix()
        except ValueError:
            return p.as_posix()
    return p.as_posix()


# -- selection --------------------------------------------------------------------


def select_tests(
    changed: Sequence[str],
    config: AffectedConfig,
    *,
    root: Path,
    base: str | None = None,
    coverage: Mapping[str, frozenset[str]] | None = None,
    all_files: Sequence[str] | None = None,
) -> Selection:
    """Map each changed file to tests; anything unsure adds a run-all reason."""
    selection = Selection(base=base, changed=tuple(changed))
    if not changed:
        selection.run_all_reasons.append("no changed files were found to narrow the run down")
        return selection
    known: list[str] | None = list(all_files) if all_files is not None else None

    def decide(path: str, outcome: str, reason: str, tests: Sequence[str] = ()) -> None:
        selection.files.append(FileDecision(path, outcome, reason, tuple(tests)))
        if outcome in ("run_all", "unmapped"):
            selection.run_all_reasons.append(reason)
        for test in tests:
            reasons = selection.tests.setdefault(test, [])
            why = f"{path}: {reason}"
            if why not in reasons:
                reasons.append(why)

    for path in changed:
        pattern = matches(path, config.run_all_when)
        if pattern:
            decide(path, "run_all", f"{path} matches run_all_when '{pattern}'")
            continue
        pattern = matches(path, config.ignore)
        if pattern:
            decide(path, "ignored", f"matches ignore '{pattern}', needs no test")
            continue
        if _is_test_file(path, config.test_files):
            if (root / path).exists():
                decide(path, "test_file", "a changed test file", [path])
            else:
                decide(path, "deleted_test", "a deleted test file, nothing to run")
            continue
        rule_tests: list[str] = []
        missing: list[str] = []
        for rule in config.rules:
            changed_pattern = matches(path, rule.changed)
            if not changed_pattern:
                continue
            for target in rule.tests:
                if "::" in target:
                    rule_tests.append(target)
                    continue
                if known is None:
                    known = list(repo_files(root))
                found = [f for f in known if _glob_regex(target).match(f)]
                if any(ch in target for ch in "*?["):
                    # "tests/payments/**" also matches __init__.py and helpers that
                    # pytest never collects, which used to force a run of everything.
                    found = [f for f in found if _is_test_file(f, config.test_files)] or found
                if found:
                    rule_tests.extend(found)
                else:
                    missing.append(target)
        if missing:
            decide(
                path,
                "run_all",
                f"the rule for {path} names tests that don't exist ({', '.join(missing)})",
            )
            continue
        if rule_tests:
            decide(path, "rule", "matched a rule in affected.rules", sorted(set(rule_tests)))
            continue
        if coverage is not None and path in coverage:
            covered = sorted(coverage[path])
            if covered:
                decide(path, "coverage", "these tests ran its code (coverage report)", covered)
            else:
                decide(path, "unmapped", f"no test ran a line of {path} in the coverage report")
            continue
        why = "no rule or test-file pattern maps it"
        if coverage is not None:
            why += ", and the coverage report doesn't list it"
        decide(path, "unmapped", f"{path} changed and {why}")

    _merge_ids_into_files(selection)
    return selection


def _merge_ids_into_files(selection: Selection) -> None:
    """``tests/a.py`` already runs ``tests/a.py::test_b``: keep the file, with both reasons."""
    for test in list(selection.tests):
        file_part = test.split("::", 1)[0]
        if file_part != test and file_part in selection.tests:
            for reason in selection.tests.pop(test):
                if reason not in selection.tests[file_part]:
                    selection.tests[file_part].append(reason)


def compute(
    start: Path,
    config: AffectedConfig,
    *,
    base: str | None = None,
    changed: Sequence[str] | None = None,
    coverage_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Selection, Path]:
    """What ``qasight affected`` and the pytest plugin do: diff, coverage, selection.

    Returns the selection and the folder its paths are relative to (the repo
    root). A git problem is a run-all reason, not an error: running all is safe.
    A coverage report that can't be read raises :class:`AffectedError`.
    """
    root = repo_root(start)
    if root is None:
        root = start.resolve()
        if changed is None:
            selection = Selection(base=base, changed=())
            selection.run_all_reasons.append(f"{root} is not in a git repository")
            return selection, root
    coverage_file = coverage_path or config.coverage
    coverage = load_coverage_map(coverage_file, root) if coverage_file else None
    if changed is None:
        base = default_base(env or {}, base or config.base, root)
        try:
            changed = changed_files(root, base)
        except AffectedError as exc:
            selection = Selection(base=base, changed=())
            selection.run_all_reasons.append(f"could not diff against {base}: {exc}")
            return selection, root
    return select_tests(changed, config, root=root, base=base, coverage=coverage), root
