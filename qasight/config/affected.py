"""Parse the ``affected:`` section: how changed files map to tests (``qasight affected``).

Every key is optional, and the section only matters to ``qasight affected`` and
``pytest --qasight-affected``; the selection itself is in
:mod:`qasight.services.affected_tests`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qasight.config.errors import ConfigValidationError

# Files that are tests themselves, by name. A changed one runs; it needs no rule.
DEFAULT_TEST_FILES: tuple[str, ...] = (
    "test_*.py",
    "*_test.py",
    "*.test.js",
    "*.test.jsx",
    "*.test.ts",
    "*.test.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.ts",
    "*.spec.tsx",
    "*.cy.js",
    "*.cy.ts",
    "*_test.go",
    "*_spec.rb",
    "*.feature",
)
# Files that can change any test. ``run_all_when`` adds to these.
DEFAULT_RUN_ALL_WHEN: tuple[str, ...] = (
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
    "qasight.yaml",
    "qasight.yml",
    "testosterone.yaml",
    "testosterone.yml",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "go.mod",
    "go.sum",
)
# Files no test reads: build and cache output that isn't in .gitignore. ``ignore`` adds to these.
DEFAULT_IGNORE: tuple[str, ...] = (
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".coverage",
    ".DS_Store",
)
_KEYS = frozenset({"base", "coverage", "run_all_when", "ignore", "test_files", "rules"})


@dataclass(frozen=True)
class AffectedRule:
    changed: tuple[str, ...]
    # Globs of test files, or pytest node ids (``tests/a.py::test_b``).
    tests: tuple[str, ...]


@dataclass(frozen=True)
class AffectedConfig:
    base: str | None = None
    coverage: Path | None = None
    run_all_when: tuple[str, ...] = DEFAULT_RUN_ALL_WHEN
    ignore: tuple[str, ...] = DEFAULT_IGNORE
    test_files: tuple[str, ...] = DEFAULT_TEST_FILES
    rules: tuple[AffectedRule, ...] = ()


def _globs(raw: Any, *, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(g, str) and g.strip() for g in raw):
        raise ConfigValidationError(f"'affected.{key}' must be a glob or a list of globs.")
    return tuple(g.strip() for g in raw)


def parse_affected(raw: Any, *, config_dir: Path) -> AffectedConfig:
    """``affected:`` (``None`` gives the defaults); ``coverage`` is relative to the config file."""
    if raw is None:
        return AffectedConfig()
    if not isinstance(raw, dict):
        raise ConfigValidationError("'affected:' must be a mapping.")
    unknown = set(raw) - _KEYS
    if unknown:
        raise ConfigValidationError(f"'affected:' has unknown keys: {', '.join(sorted(unknown))}.")
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise ConfigValidationError("'affected.rules' must be a list.")
    rules: list[AffectedRule] = []
    for i, item in enumerate(rules_raw):
        if not isinstance(item, dict) or set(item) - {"changed", "tests"}:
            raise ConfigValidationError(
                f"'affected.rules[{i}]' must be a mapping with 'changed' and 'tests'."
            )
        changed = _globs(item.get("changed"), key=f"rules[{i}].changed")
        tests = _globs(item.get("tests"), key=f"rules[{i}].tests")
        if not changed or not tests:
            raise ConfigValidationError(f"'affected.rules[{i}]' needs both 'changed' and 'tests'.")
        rules.append(AffectedRule(changed=changed, tests=tests))
    base = str(raw.get("base") or "").strip()
    coverage = str(raw.get("coverage") or "").strip()
    return AffectedConfig(
        base=base or None,
        coverage=(config_dir / coverage) if coverage else None,
        run_all_when=DEFAULT_RUN_ALL_WHEN + _globs(raw.get("run_all_when"), key="run_all_when"),
        ignore=DEFAULT_IGNORE + _globs(raw.get("ignore"), key="ignore"),
        test_files=_globs(raw.get("test_files"), key="test_files") or DEFAULT_TEST_FILES,
        rules=tuple(rules),
    )
