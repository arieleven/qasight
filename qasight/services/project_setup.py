"""Look at a project and work out how to add QASight to it.

Used by ``qasight init`` (and the snippets in the docs, which render from the
same tables). Two questions, both answered from files only, nothing is run:

* :func:`detect_stacks` — which test runners the project uses (pytest,
  Jest, Vitest, Playwright, ``go test``, ``dotnet test``, Maven, Gradle,
  RSpec, PHPUnit), from ``pyproject.toml``, ``package.json``, ``go.mod``,
  ``*.csproj``, ``pom.xml`` and friends. Each :class:`Stack` knows the
  ``qasight.yaml`` stage that runs it and writes results QASight
  reads, and how CI installs it.
* :func:`find_result_files` — result files a test run already left in the
  project (JUnit XML, Jest/Vitest/Playwright/RSpec JSON, ``.trx``,
  ``go test -json``, Robot ``output.xml``, ``allure-results``), recognised by
  their content. Files committed to git are skipped: those are fixtures, not
  the last run's results.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Folders that never hold a project's own results (or are too big to walk).
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".tox",
        ".nox",
        "site-packages",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".testo",
        "artifacts",
        "dist",
        ".next",
        ".gradle",
        "vendor",
        "bin",
        "obj",
    }
)
_RESULT_SUFFIXES = frozenset({".xml", ".json", ".trx", ".jsonl", ".log", ".txt"})
_MAX_WALK_FILES = 20_000
_MAX_RESULT_BYTES = 200 * 1024 * 1024
_SNIFF_BYTES = 64 * 1024


@dataclass(frozen=True)
class Stack:
    """One test runner a project uses, and how to run it so QASight reads its results."""

    key: str
    label: str
    language: str  # python, node, go, dotnet, java, ruby, php
    stage: dict[str, Any]  # the qasight.yaml stage
    install: tuple[str, ...] = ()  # shell commands that install the project's test dependencies
    result_files: tuple[str, ...] = ()  # where the stage writes its results (globs)
    note: str = ""


@dataclass(frozen=True)
class ProjectFacts:
    """What :func:`detect_stacks` read, kept for the CI templates (versions, package manager)."""

    stacks: tuple[Stack, ...]
    python_version: str = "3.12"
    node_version_file: str = ""
    node_package_manager: str = "npm"
    go_version_file: str = ""
    dotnet_version: str = "8.0.x"
    java_build: str = ""  # maven or gradle
    skipped: tuple[str, ...] = field(default_factory=tuple)


STACK_KEYS: tuple[str, ...] = (
    "pytest",
    "jest",
    "vitest",
    "playwright",
    "go",
    "dotnet",
    "maven",
    "gradle",
    "rspec",
    "phpunit",
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _package_json(repo: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read_text(repo / "package.json") or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _node_deps(pkg: dict[str, Any]) -> set[str]:
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = pkg.get(key)
        if isinstance(value, dict):
            deps |= {str(k) for k in value}
    return deps


def _node_package_manager(repo: Path, pkg: dict[str, Any]) -> str:
    declared = str(pkg.get("packageManager") or "")
    for name in ("pnpm", "yarn", "bun"):
        if declared.startswith(name):
            return name
    if (repo / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (repo / "yarn.lock").is_file():
        return "yarn"
    if (repo / "bun.lockb").is_file() or (repo / "bun.lock").is_file():
        return "bun"
    return "npm"


def node_install_command(manager: str, repo: Path) -> str:
    if manager == "pnpm":
        return "pnpm install --frozen-lockfile"
    if manager == "yarn":
        return "yarn install --frozen-lockfile"
    if manager == "bun":
        return "bun install --frozen-lockfile"
    return "npm ci" if (repo / "package-lock.json").is_file() else "npm install"


def _pyproject(repo: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(_read_text(repo / "pyproject.toml") or "")
    except tomllib.TOMLDecodeError:
        return {}


def _python_version(repo: Path, pyproject: dict[str, Any]) -> str:
    pinned = _read_text(repo / ".python-version").strip().splitlines()
    if pinned and re.fullmatch(r"3\.\d+(\.\d+)?", pinned[0].strip()):
        return ".".join(pinned[0].strip().split(".")[:2])
    requires = str((pyproject.get("project") or {}).get("requires-python") or "")
    match = re.search(r">=\s*3\.(\d+)", requires)
    if match and int(match.group(1)) > 12:
        return f"3.{match.group(1)}"
    return "3.12"


def _uses_pytest(repo: Path, pyproject: dict[str, Any]) -> bool:
    if (repo / "pytest.ini").is_file() or (repo / "conftest.py").is_file():
        return True
    if (pyproject.get("tool") or {}).get("pytest") is not None:
        return True
    for name in ("setup.cfg", "tox.ini"):
        if "[tool:pytest]" in _read_text(repo / name) or "[pytest]" in _read_text(repo / name):
            return True
    blobs = [_read_text(repo / "pyproject.toml"), _read_text(repo / "setup.py")]
    blobs += [_read_text(p) for p in sorted(repo.glob("requirements*.txt"))]
    if any(re.search(r"(?im)^\s*[\"']?pytest\b", b) or '"pytest' in b for b in blobs):
        return True
    tests = repo / "tests"
    return tests.is_dir() and any(tests.rglob("test_*.py"))


def _python_install(repo: Path, pyproject: dict[str, Any]) -> tuple[str, ...]:
    steps: list[str] = []
    for name in ("requirements-dev.txt", "requirements-test.txt", "requirements.txt"):
        if (repo / name).is_file():
            steps.append(f"pip install -r {name}")
            break
    if not steps and ((repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file()):
        extras = (pyproject.get("project") or {}).get("optional-dependencies") or {}
        extra = next((e for e in ("test", "tests", "dev") if e in extras), "")
        steps.append(f'pip install -e ".[{extra}]"' if extra else "pip install -e .")
    if not any("pytest" in _read_text(repo / s.split()[-1]) for s in steps if " -r " in s):
        steps.append("pip install pytest")
    return tuple(steps)


def _pytest_stack(repo: Path, pyproject: dict[str, Any]) -> Stack:
    args = ["-q"]
    if (repo / "tests").is_dir():
        args.append("tests")
    return Stack(
        key="pytest",
        label="pytest",
        language="python",
        stage={"name": "pytest", "equipment": "pytest", "args": args},
        install=_python_install(repo, pyproject),
    )


def _csproj_is_test(path: Path) -> bool:
    text = _read_text(path)
    return any(
        marker in text
        for marker in ("Microsoft.NET.Test.Sdk", "xunit", "NUnit", "MSTest", "<IsTestProject>true")
    )


def _walk_limited(repo: Path, pattern: str, *, depth: int = 4) -> list[Path]:
    found: list[Path] = []
    for path in repo.glob(pattern):
        rel = path.relative_to(repo).parts
        if len(rel) > depth or any(part in _SKIP_DIRS for part in rel[:-1]):
            continue
        found.append(path)
    return sorted(found)


def detect_project(repo: Path, *, only: Iterable[str] = ()) -> ProjectFacts:
    """The stacks *repo* uses (or just *only*, when given), and the facts CI templates need."""
    repo = repo.expanduser().resolve()
    wanted = {s.strip().lower() for s in only if s.strip()}
    pyproject = _pyproject(repo)
    pkg = _package_json(repo)
    deps = _node_deps(pkg)
    stacks: list[Stack] = []
    skipped: list[str] = []

    def want(key: str, found: bool) -> bool:
        return key in wanted if wanted else found

    if want("pytest", _uses_pytest(repo, pyproject)):
        stacks.append(_pytest_stack(repo, pyproject))

    manager = _node_package_manager(repo, pkg)
    node_install = (node_install_command(manager, repo),)
    if want("jest", "jest" in deps):
        stacks.append(
            Stack(
                key="jest",
                label="Jest",
                language="node",
                stage={"name": "jest", "equipment": "jest", "args": ["--ci"]},
                install=node_install,
            )
        )
    if want("vitest", "vitest" in deps):
        stacks.append(
            Stack(
                key="vitest",
                label="Vitest",
                language="node",
                stage={"name": "vitest", "equipment": "vitest", "args": []},
                install=node_install,
            )
        )
    if want("playwright", "@playwright/test" in deps):
        stacks.append(
            Stack(
                key="playwright",
                label="Playwright",
                language="node",
                stage={"name": "playwright", "equipment": "playwright", "args": [], "tier": "e2e"},
                install=(*node_install, "npx playwright install --with-deps"),
            )
        )
    if not wanted and "cypress" in deps:
        skipped.append(
            "Cypress: left out because it needs your app running first; add an "
            "`equipment: cypress` stage once CI starts the app."
        )

    if want("go", (repo / "go.mod").is_file()):
        stacks.append(
            Stack(
                key="go",
                label="go test",
                language="go",
                stage={
                    "name": "go",
                    "equipment": "command",
                    "args": [
                        "sh",
                        "-c",
                        "mkdir -p test-results && go test -json ./... > test-results/go-test.json",
                    ],
                    "junit_xml": ["test-results/go-test.json"],
                },
                result_files=("test-results/go-test.json",),
            )
        )

    test_projects = [p for p in _walk_limited(repo, "**/*.csproj") if _csproj_is_test(p)]
    if want("dotnet", bool(test_projects)):
        stacks.append(
            Stack(
                key="dotnet",
                label="dotnet test",
                language="dotnet",
                stage={
                    "name": "dotnet",
                    "equipment": "command",
                    "args": [
                        "dotnet",
                        "test",
                        "--logger",
                        "trx",
                        "--results-directory",
                        "test-results/dotnet",
                    ],
                    "junit_xml": ["test-results/dotnet/*.trx"],
                },
                result_files=("test-results/dotnet/*.trx",),
            )
        )

    java_build = ""
    if want("maven", (repo / "pom.xml").is_file()):
        java_build = "maven"
        mvn = "./mvnw" if (repo / "mvnw").is_file() else "mvn"
        stacks.append(
            Stack(
                key="maven",
                label="Maven (Surefire)",
                language="java",
                stage={
                    "name": "maven",
                    "equipment": "command",
                    "args": [mvn, "-B", "test"],
                    "junit_xml": ["**/target/surefire-reports/TEST-*.xml"],
                },
                result_files=("**/target/surefire-reports/TEST-*.xml",),
            )
        )
    gradle_found = (repo / "build.gradle").is_file() or (repo / "build.gradle.kts").is_file()
    if want("gradle", gradle_found):
        java_build = java_build or "gradle"
        gradle = "./gradlew" if (repo / "gradlew").is_file() else "gradle"
        stacks.append(
            Stack(
                key="gradle",
                label="Gradle",
                language="java",
                stage={
                    "name": "gradle",
                    "equipment": "command",
                    "args": [gradle, "test"],
                    "junit_xml": ["**/build/test-results/test/TEST-*.xml"],
                },
                result_files=("**/build/test-results/test/TEST-*.xml",),
            )
        )

    gemfile = _read_text(repo / "Gemfile") + _read_text(repo / "Gemfile.lock")
    if want("rspec", "rspec" in gemfile or (repo / ".rspec").is_file()):
        stacks.append(
            Stack(
                key="rspec",
                label="RSpec",
                language="ruby",
                stage={
                    "name": "rspec",
                    "equipment": "command",
                    "args": [
                        "bundle",
                        "exec",
                        "rspec",
                        "--format",
                        "progress",
                        "--format",
                        "json",
                        "--out",
                        "test-results/rspec.json",
                    ],
                    "junit_xml": ["test-results/rspec.json"],
                },
                install=("bundle install",),
                result_files=("test-results/rspec.json",),
            )
        )

    composer = _read_text(repo / "composer.json")
    if want("phpunit", "phpunit/phpunit" in composer):
        stacks.append(
            Stack(
                key="phpunit",
                label="PHPUnit",
                language="php",
                stage={
                    "name": "phpunit",
                    "equipment": "command",
                    "args": ["vendor/bin/phpunit", "--log-junit", "test-results/phpunit.xml"],
                    "junit_xml": ["test-results/phpunit.xml"],
                },
                install=("composer install --no-interaction --prefer-dist",),
                result_files=("test-results/phpunit.xml",),
            )
        )

    node_version_file = next((n for n in (".nvmrc", ".node-version") if (repo / n).is_file()), "")
    return ProjectFacts(
        stacks=tuple(stacks),
        python_version=_python_version(repo, pyproject),
        node_version_file=node_version_file,
        node_package_manager=manager,
        go_version_file="go.mod" if (repo / "go.mod").is_file() else "",
        java_build=java_build,
        skipped=tuple(skipped),
    )


def detect_stacks(repo: Path) -> tuple[Stack, ...]:
    """The test runners *repo* uses, in a fixed order (Python, Node, Go, .NET, Java, Ruby, PHP)."""
    return detect_project(repo).stacks


def cycle_name_for(repo: Path) -> str:
    """The default cycle name: the project folder's name, as ``pytest --qasight`` uses it."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo.expanduser().resolve().name).strip("-.")
    return name.lower() or "tests"


# --- result files --------------------------------------------------------------------------


def _git_tracked(repo: Path) -> set[Path] | None:
    """Files git tracks under *repo* (fixtures, not results), or ``None`` outside a checkout."""
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "-C", str(repo), "ls-files", "-z", "--full-name"],  # noqa: S607
            check=False,
            capture_output=True,
            timeout=30,
        )
        top = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],  # noqa: S607
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or top.returncode != 0:
        return None
    root = Path(top.stdout.strip()).resolve()
    return {(root / name).resolve() for name in proc.stdout.decode().split("\0") if name}


def looks_like_results(path: Path) -> bool:
    """Whether *path* reads like a test result file QASight imports (cheap content check)."""
    try:
        if path.stat().st_size > _MAX_RESULT_BYTES:
            return False
        with path.open("rb") as fh:
            head = fh.read(_SNIFF_BYTES)
    except OSError:
        return False
    text = head.decode("utf-8", errors="replace")
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = head.decode("utf-16", errors="replace")
    stripped = text.lstrip("﻿ \t\r\n")
    if stripped.startswith("<"):
        return bool(re.search(r"<(testsuites?|TestRun|robot)[\s>]", stripped))
    if path.name.endswith("-result.json") and '"uuid"' in text and '"status"' in text:
        return True  # an Allure result
    if '"Action"' in text and '"Package"' in text:
        return True  # go test -json
    if stripped.startswith("{"):
        return (
            '"numTotalTests"' in text  # Jest / Vitest
            or ('"examples"' in text and '"full_description"' in text)  # RSpec
            or ('"suites"' in text and '"config"' in text)  # Playwright
        )
    return False


def find_result_files(repo: Path, *, include_tracked: bool = False) -> list[Path]:
    """Result files a test run left under *repo*, newest first; git-tracked files are skipped."""
    repo = repo.expanduser().resolve()
    tracked = None if include_tracked else _git_tracked(repo)
    found: list[Path] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            seen += 1
            if seen > _MAX_WALK_FILES:
                break
            path = Path(dirpath) / name
            if path.suffix.lower() not in _RESULT_SUFFIXES or name in {
                "package.json",
                "package-lock.json",
                "tsconfig.json",
                "composer.json",
            }:
                continue
            if tracked is not None and path.resolve() in tracked:
                continue
            if looks_like_results(path):
                found.append(path.resolve())
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found


def result_patterns(files: Iterable[Path], *, repo: Path) -> list[str]:
    """*files* as ``qasight ingest`` arguments relative to *repo*; an Allure folder becomes one entry."""
    repo = repo.expanduser().resolve()
    out: list[str] = []
    for path in files:
        rel = path.resolve().relative_to(repo)
        if path.name.endswith("-result.json"):
            entry = rel.parent.as_posix() or "."
        else:
            entry = rel.as_posix()
        if entry not in out:
            out.append(entry)
    return out


# --- qasight.yaml ---------------------------------------------------------------------


def _yaml_list(values: Iterable[str]) -> str:
    return "[" + ", ".join(json.dumps(str(v)) for v in values) + "]"


def config_yaml(facts: ProjectFacts, *, cycle: str) -> str:
    """A ``qasight.yaml`` with one *cycle* that runs every detected stack, in order."""
    from qasight.config.json_schema import SCHEMA_ID

    names = ", ".join(dict.fromkeys(s.label for s in facts.stacks))
    lines = [
        f"# yaml-language-server: $schema={SCHEMA_ID}",
        f"# Written by `qasight init` for: {names}.",
        "# Run it with `qasight run`; every key is in the Command Reference:",
        "# https://arieleven.github.io/qasight/docs/cli-commands/command-reference/",
        "version: 1",
        "cycles:",
        f"  {json.dumps(cycle)}:",
        "    description: Every test suite in this project (found by qasight init).",
        "    stages:",
    ]
    for stack in facts.stacks:
        stage = stack.stage
        lines.append(f"      - name: {json.dumps(stage['name'])}")
        lines.append(f"        equipment: {stage['equipment']}")
        if stage.get("target_repo"):
            lines.append(f"        target_repo: {json.dumps(str(stage['target_repo']))}")
        lines.append(f"        args: {_yaml_list(stage.get('args') or [])}")
        if stage.get("junit_xml"):
            lines.append(f"        junit_xml: {_yaml_list(stage['junit_xml'])}")
        if stage.get("tier"):
            lines.append(f"        tier: {stage['tier']}")
    for note in facts.skipped:
        lines.append(f"# Not added: {note}")
    return "\n".join(lines) + "\n"


GITIGNORE_LINES: tuple[str, ...] = ("testo_history.db", "artifacts/", ".testo/", "test-results/")


def missing_gitignore_lines(repo: Path) -> list[str]:
    """Which of QASight's local files ``.gitignore`` doesn't ignore yet."""
    existing = {
        line.strip().lstrip("/")
        for line in _read_text(repo / ".gitignore").splitlines()
        if line.strip() and not line.startswith("#")
    }
    return [
        line
        for line in GITIGNORE_LINES
        if line not in existing and line.rstrip("/") not in existing
    ]
