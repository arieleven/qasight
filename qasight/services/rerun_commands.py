"""A command that reruns a run's failing tests, per runner, for the run page's "Rerun these".

Built from what the run recorded: each failing test's stage, that stage's
framework, the framework its result file named, and the test's names. Only
runners whose command can name single tests are covered:

    pytest              node ids
    Playwright          ``npx playwright test --grep``
    Behave / BehaveX    ``--name``
    Jest / Vitest       test files plus ``-t`` on the full ``describe`` path
    go test             ``go test -run '^(TestA|TestB)$' <packages>`` (top-level tests)
    .NET                ``dotnet test --filter FullyQualifiedName=...``
    Robot Framework     ``robot --test <name> <root suite folder>``
    RSpec               ``rspec <spec files> -e <full description>`` (``bundle exec``
                        when the repository has a ``Gemfile``)
    Android (Espresso)  ``./gradlew connectedAndroidTest`` with the instrumentation
                        runner's ``class=Class#method`` argument
    JUnit 4/5, TestNG   ``mvn test -Dtest=Class#method`` or ``gradle test --tests``,
                        when the repository has a ``pom.xml`` or a Gradle build

JUnit files ingested from other runners get a pytest command when their names
point at a Python test module. Anything else is left out rather than guessed:
k6 checks and thresholds (only the whole script reruns) and XCTest (``xcodebuild``
also needs a scheme and a destination the results don't record).
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from qasight.services.test_ownership import source_path

# A command line people paste stays readable; past this, rerun the stage.
MAX_TESTS = 30
# Regex metacharacters, the same in Python (Behave) and JavaScript (Playwright).
_REGEX_SPECIAL = re.compile(r"[.*+?^${}()|\[\]\\/]")


_PYTEST_FILE = re.compile(r"test_\w*\.py|\w+_test\.py")
# Frameworks a result file can name for itself (``framework`` label), whatever stage read it.
_SELF_NAMED = frozenset(
    {"playwright", "jest", "vitest", "go", "dotnet", "robot", "rspec", "android"}
)
# Java method names a build tool can select (not a @DisplayName with spaces).
_JAVA_METHOD = re.compile(r"[A-Za-z_$][\w$]*")


@dataclass(frozen=True)
class FailingCase:
    name: str
    full_name: str
    group: str
    stage: str
    # The ``framework`` label its result carried (``jest``, ``go``, ``dotnet``...), if any.
    framework: str = ""


def pytest_node_id(full_name: str, name: str, group: str = "") -> str | None:
    """``tests/api/test_orders.py::TestX::test_y`` from Allure or JUnit names, if they point at a module."""
    if "::" in full_name:
        return full_name
    path = source_path(full_name, group)
    # pytest only collects test_*.py / *_test.py, so "com.acme.CartTest.adds" is not one.
    if path is None or not _PYTEST_FILE.fullmatch(path.rsplit("/", 1)[-1]):
        return None
    module = path[: -len(".py")].replace("/", ".")
    dotted = full_name.replace("#", ".")
    rest = dotted[len(module) + 1 :] if dotted.startswith(module + ".") else name
    return f"{path}::{'::'.join(part for part in rest.split('.') if part)}"


def _pytest(cases: list[FailingCase]) -> str | None:
    ids = [pytest_node_id(c.full_name, c.name, c.group) for c in cases]
    if not ids or any(i is None for i in ids):
        return None
    return shlex.join(["pytest", *dict.fromkeys(i for i in ids if i)])


def _literal(text: str) -> str:
    """``text`` as a regex that matches it exactly, readable (spaces stay spaces)."""
    return _REGEX_SPECIAL.sub(lambda m: "\\" + m.group(0), text)


def _grep(titles: Iterable[str]) -> str:
    return "|".join(_literal(t) for t in dict.fromkeys(titles))


def _playwright(cases: list[FailingCase]) -> str:
    return shlex.join(["npx", "playwright", "test", "--grep", _grep(c.name for c in cases)])


def _behave(cases: list[FailingCase], command: str) -> str:
    args = [command]
    for name in dict.fromkeys(c.name for c in cases):
        args += ["--name", f"^{_literal(name)}$"]
    return shlex.join(args)


def _js_parts(case: FailingCase) -> tuple[str, str]:
    """(test file, full ``describe`` path joined by spaces) from ``file › describe › title``."""
    parts = case.full_name.split(" › ")
    if len(parts) < 2:
        return "", case.name
    return parts[0], " ".join(parts[1:])


def _jest(cases: list[FailingCase], framework: str) -> str | None:
    pairs = [_js_parts(c) for c in cases]
    files = list(dict.fromkeys(f for f, _ in pairs if f))
    if not files:
        return None
    pattern = f"^({_grep(t for _, t in pairs)})$"
    base = ["npx", "jest"] if framework == "jest" else ["npx", "vitest", "run"]
    return shlex.join([*base, *files, "-t", pattern])


def _go(cases: list[FailingCase]) -> str | None:
    packages: list[str] = []
    tops: list[str] = []
    for case in cases:
        package, dot, test = case.full_name.rpartition(".")
        if not dot or not test.startswith(("Test", "Example", "Fuzz")):
            return None
        packages.append(package)
        tops.append(test.split("/", 1)[0])
    regex = "|".join(dict.fromkeys(tops))
    return shlex.join(["go", "test", "-run", f"^({regex})$", *dict.fromkeys(packages)])


def _dotnet(cases: list[FailingCase]) -> str | None:
    names: list[str] = []
    for case in cases:
        name = re.sub(r"\(.*\)$", "", case.full_name).strip()
        if not name or any(ch in name for ch in "&|=!~ "):
            return None
        names.append(f"FullyQualifiedName={name}")
    return shlex.join(["dotnet", "test", "--filter", "|".join(dict.fromkeys(names))])


def _robot(cases: list[FailingCase]) -> str | None:
    roots = {c.full_name.split(".", 1)[0] for c in cases if "." in c.full_name}
    if len(roots) != 1:
        return None
    # Robot names the root suite after its folder ("my_tests" -> "My Tests").
    folder = roots.pop().replace(" ", "_").lower()
    args = ["robot"]
    for name in dict.fromkeys(c.name for c in cases):
        args += ["--test", name]
    return shlex.join([*args, folder])


def _rspec(cases: list[FailingCase], repo: Path | None) -> str | None:
    files: list[str] = []
    descriptions: list[str] = []
    for case in cases:
        spec_file, sep, description = case.full_name.partition(" › ")
        if not sep or not spec_file.endswith(".rb"):
            return None
        files.append(spec_file)
        descriptions.append(description)
    base = ["bundle", "exec", "rspec"] if repo and (repo / "Gemfile").is_file() else ["rspec"]
    args = [*base, *dict.fromkeys(files)]
    for description in dict.fromkeys(descriptions):
        args += ["-e", description]
    return shlex.join(args)


def _android(cases: list[FailingCase], repo: Path | None) -> str | None:
    targets = [_java_target(c) for c in cases]
    if not targets or any(t is None for t in targets):
        return None
    selector = ",".join(dict.fromkeys(f"{cls}#{method}" for cls, method in filter(None, targets)))
    gradle = "./gradlew" if repo is None or (repo / "gradlew").is_file() else "gradle"
    return shlex.join(
        [
            gradle,
            "connectedAndroidTest",
            f"-Pandroid.testInstrumentationRunnerArguments.class={selector}",
        ]
    )


def _java_target(case: FailingCase) -> tuple[str, str] | None:
    """(fully qualified class, method) for a JUnit/TestNG case, if the method can be selected."""
    method = re.sub(r"\(.*\)$", "", case.name).strip()
    prefix = (
        case.full_name[: -len(case.name) - 1] if case.full_name.endswith("." + case.name) else ""
    )
    if not prefix or not _JAVA_METHOD.fullmatch(method):
        return None
    return prefix, method


def _java(cases: list[FailingCase], build_tool: str) -> str | None:
    targets = [_java_target(c) for c in cases]
    if not targets or any(t is None for t in targets):
        return None
    pairs = [t for t in targets if t is not None]
    if build_tool == "maven":
        by_class: dict[str, list[str]] = {}
        for cls, method in pairs:
            by_class.setdefault(cls.rsplit(".", 1)[-1], []).append(method)
        selector = ",".join(f"{c}#{'+'.join(dict.fromkeys(ms))}" for c, ms in by_class.items())
        return shlex.join(
            ["mvn", "test", f"-Dtest={selector}", "-Dsurefire.failIfNoSpecifiedTests=false"]
        )
    args = [build_tool, "test"]
    for cls, method in dict.fromkeys(pairs):
        args += ["--tests", f"{cls}.{method}"]
    return shlex.join(args)


def java_build_tool(repo: Path | None) -> str | None:
    """``maven``, ``./gradlew`` or ``gradle`` from the files at the root of ``repo``."""
    if repo is None or not repo.is_dir():
        return None
    if (repo / "pom.xml").is_file():
        return "maven"
    if (repo / "build.gradle").is_file() or (repo / "build.gradle.kts").is_file():
        return "./gradlew" if (repo / "gradlew").is_file() else "gradle"
    return None


def rerun_commands(
    cases: list[FailingCase], frameworks: dict[str, str], *, repo: Path | None = None
) -> list[str]:
    """One command per runner that can rerun ``cases``; ``frameworks`` maps stage name to framework.

    ``repo`` (the run's target repository, when it is on this machine) tells
    Maven from Gradle for JUnit results that came from a JVM build.
    """
    if not cases or len(cases) > MAX_TESTS:
        return []
    build_tool: str | None = None
    by_framework: dict[str, list[FailingCase]] = {}
    for case in cases:
        framework = frameworks.get(case.stage, "")
        if framework in ("junit", "command", ""):
            # Ingested or wrapped: the result file's own framework, else what the names say.
            if case.framework in _SELF_NAMED:
                framework = case.framework
            elif pytest_node_id(case.full_name, case.name, case.group):
                framework = "pytest"
            else:
                if build_tool is None:
                    build_tool = java_build_tool(repo) or ""
                framework = "java" if build_tool and _java_target(case) else ""
        by_framework.setdefault(framework, []).append(case)
    commands: list[str] = []
    for framework, group in by_framework.items():
        command: str | None
        if framework == "pytest":
            command = _pytest(group)
        elif framework == "playwright":
            command = _playwright(group)
        elif framework in ("behave", "behavex"):
            command = _behave(group, framework)
        elif framework in ("jest", "vitest"):
            command = _jest(group, framework)
        elif framework == "go":
            command = _go(group)
        elif framework == "dotnet":
            command = _dotnet(group)
        elif framework == "robot":
            command = _robot(group)
        elif framework == "rspec":
            command = _rspec(group, repo)
        elif framework == "android":
            command = _android(group, repo)
        elif framework == "java" and build_tool:
            command = _java(group, build_tool)
        else:
            command = None
        if command:
            commands.append(command)
    return commands
