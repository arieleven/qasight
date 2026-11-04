"""Find every test suite in a repository, folder by folder (``qasight init --scan``).

Plain ``qasight init`` reads the project files at the top of the repository
(:func:`qasight.services.project_setup.detect_project`). A monorepo keeps
them lower down: ``api/pyproject.toml``, ``web/package.json``,
``services/billing/go.mod``. :func:`scan_repo` walks the folders, runs the
same detection in each folder that has a project file, and gives back one
:class:`FoundStage` per runner per folder, with ``target_repo`` set so the
stage starts in its folder.

Only files are read; nothing is run. Two folders can name the same tests (a
Maven parent and its modules, a root Jest config and a workspace package);
the rules in :func:`_covered_by` keep the outer one and say so.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from qasight.services.project_setup import (
    _SKIP_DIRS,
    ProjectFacts,
    Stack,
    _csproj_is_test,
    detect_project,
)

# A folder with one of these is looked at as a project of its own.
_MARKERS = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pytest.ini",
        "tox.ini",
        "conftest.py",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "package.json",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Gemfile",
        ".rspec",
        "composer.json",
    }
)
_MAX_DEPTH = 6
_MAX_FOLDERS = 5_000
# One run at the outer folder already picks these up in the folders below it.
_OUTER_COVERS = frozenset({"jest", "vitest", "maven", "gradle"})
# Install steps that don't depend on the folder they run in.
_ANYWHERE = frozenset({"pip install pytest"})


@dataclass(frozen=True)
class FoundStage:
    """One runner in one folder of the repository."""

    folder: str  # POSIX path relative to the repository, "." for the top
    stack: Stack  # its stage has ``target_repo`` (outside the top) and a unique name


@dataclass(frozen=True)
class ScanResult:
    found: tuple[FoundStage, ...]
    facts: ProjectFacts  # every found stack, for the config and the CI templates
    notes: tuple[str, ...] = ()


def _rel(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return rel or "."


def project_folders(root: Path) -> list[Path]:
    """Folders under *root* (itself first) that hold a project file, top-down, sorted."""
    root = root.expanduser().resolve()
    found: list[Path] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        depth = len(here.relative_to(root).parts)
        dirnames[:] = (
            []
            if depth >= _MAX_DEPTH
            else sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        )
        seen += 1
        if seen > _MAX_FOLDERS:
            break
        names = set(filenames)
        if here == root or names & _MARKERS or any(n.endswith((".csproj", ".sln")) for n in names):
            found.append(here)
    return found


def _paths_in_args(stage: dict[str, object]) -> list[str]:
    args = stage.get("args")
    if not isinstance(args, list):
        return []
    return [str(a) for a in args if not str(a).startswith("-")]


def _covered_by(found: list[FoundStage], folder: str, stack: Stack) -> FoundStage | None:
    """An outer stage that already runs *stack*'s tests in *folder*, if any."""
    here = PurePosixPath(folder)
    for outer in found:
        if outer.stack.key != stack.key or outer.folder == folder:
            continue
        base = PurePosixPath(outer.folder)
        if outer.folder != "." and base not in here.parents:
            continue
        if stack.key in _OUTER_COVERS:
            return outer
        if stack.key == "pytest":
            paths = _paths_in_args(outer.stack.stage)
            if not paths:
                return outer  # bare `pytest` collects every folder below it
            for p in paths:
                target = base / p if outer.folder != "." else PurePosixPath(p)
                if here == target or target in here.parents:
                    return outer
    return None


def _slug(folder: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", folder).strip("-.").lower() or "root"


def _in_folder(stack: Stack, folder: str, taken: set[str]) -> Stack:
    """*stack* set up to run from *folder*: ``target_repo``, a unique name, installs run there."""
    stage = dict(stack.stage)
    name = str(stage["name"])
    if folder != ".":
        stage["target_repo"] = folder
        name = f"{_slug(folder)}-{name}"
        install = tuple(
            cmd if cmd in _ANYWHERE else f"(cd {shlex.quote(folder)} && {cmd})"
            for cmd in stack.install
        )
    else:
        install = stack.install
    base, n = name, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    stage["name"] = name
    return replace(stack, stage=stage, install=install)


def _dotnet_folders(folders: list[Path]) -> set[Path]:
    """Where ``dotnet test`` should start: a folder with a ``.sln``, else each test project's folder.

    ``dotnet test`` in a folder with several projects and no solution file
    refuses to pick one, so the top folder alone isn't enough.
    """
    solutions = {f for f in folders if any(f.glob("*.sln"))}
    tests = {f for f in folders if any(_csproj_is_test(p) for p in f.glob("*.csproj"))}
    out = set(solutions)
    for f in tests:
        if not any(s == f or s in f.parents for s in solutions):
            out.add(f)
    return out


def scan_repo(root: Path, *, only: tuple[str, ...] | list[str] = ()) -> ScanResult:
    """Every runner in every project folder under *root* (or just *only*), outer folders first."""
    root = root.expanduser().resolve()
    wanted = [s.strip().lower() for s in only if s.strip()]
    folders = project_folders(root)
    dotnet_at = _dotnet_folders(folders)
    found: list[FoundStage] = []
    notes: list[str] = []
    taken: set[str] = set()
    first_facts: dict[str, tuple[str, ProjectFacts]] = {}

    for path in folders:
        folder = _rel(path, root)
        facts = detect_project(path)
        stacks = [s for s in facts.stacks if s.key != "dotnet"]
        if path in dotnet_at:
            stacks += list(detect_project(path, only=["dotnet"]).stacks)
        if wanted:
            stacks = [s for s in stacks if s.key in wanted]
        where = "the top folder" if folder == "." else folder
        notes += [f"{where}: {note}" for note in facts.skipped]
        for stack in stacks:
            outer = _covered_by(found, folder, stack)
            if outer is not None:
                notes.append(
                    f"{folder}: {stack.label} left out; the {outer.stack.stage['name']!r} "
                    f"stage in {'the top folder' if outer.folder == '.' else outer.folder} "
                    "already runs it"
                )
                continue
            found.append(FoundStage(folder=folder, stack=_in_folder(stack, folder, taken)))
            first_facts.setdefault(stack.language, (folder, facts))

    return ScanResult(
        found=tuple(found),
        facts=_merged_facts(found, first_facts),
        notes=tuple(notes),
    )


def _merged_facts(
    found: list[FoundStage], first: dict[str, tuple[str, ProjectFacts]]
) -> ProjectFacts:
    """One :class:`ProjectFacts` for the CI templates: each language's versions from its first folder."""

    def at(folder: str, name: str) -> str:
        return name if not name or folder == "." else f"{folder}/{name}"

    kwargs: dict[str, str] = {}
    if "python" in first:
        kwargs["python_version"] = first["python"][1].python_version
    if "node" in first:
        folder, facts = first["node"]
        kwargs["node_version_file"] = at(folder, facts.node_version_file)
        kwargs["node_package_manager"] = facts.node_package_manager
    if "go" in first:
        folder, facts = first["go"]
        kwargs["go_version_file"] = at(folder, facts.go_version_file)
    if "java" in first:
        kwargs["java_build"] = first["java"][1].java_build
    return ProjectFacts(stacks=tuple(f.stack for f in found), **kwargs)  # type: ignore[arg-type]
