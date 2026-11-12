"""``qasight run -- <command>``: run any test command and record what it ran, no config needed.

:func:`prepare` looks at the command (``pytest -q``, ``npm test``, ``go test
./...``, ``mvn test`` ...), works out the runner, and adds what that runner needs
to also write a result file QASight reads (``--junitxml``, Jest's JSON report,
``go test -json``, a TRX logger ...) into a fresh folder under ``.testo/wrap/``.
The command then runs as it is otherwise, with its own output and exit code;
the CLI records the files it left through ``qasight ingest``'s path.

A runner it doesn't know still runs: result files that appeared under the
working directory while it ran (found by content, as ``qasight ingest`` does)
are recorded instead.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass
class Wrapped:
    """The command to run, and where its results will be."""

    runner: str  # pytest, jest, vitest, playwright, go, dotnet, maven, gradle, rspec, phpunit, ""
    label: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)  # result files it will write
    globs: list[str] = field(default_factory=list)  # result files it writes already (newer = ours)
    go_json: bool = False  # `go test -json`: print its output as plain text, keep the JSON
    shell: bool = False
    tier: str = "unit"


def _has(argv: list[str], *prefixes: str) -> bool:
    return any(a == p or a.startswith(p + "=") for a in argv for p in prefixes)


def _package_scripts(cwd: Path) -> dict[str, str]:
    try:
        data = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    scripts = data.get("scripts") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in scripts.items()} if isinstance(scripts, dict) else {}


def _runner_word(argv: list[str]) -> int:
    """Index of the word that names the program, past ``env``, ``VAR=value`` and launchers."""
    i = 0
    while i < len(argv) and (argv[i] == "env" or _ENV_ASSIGN.match(argv[i])):
        i += 1
    return i


def _node_runner(words: list[str], cwd: Path) -> tuple[str, bool]:
    """``(jest|vitest|playwright|"", via_npm_script)`` for a Node command."""
    if not words:
        return "", False
    tool = Path(words[0]).name
    rest = words[1:]
    if tool in ("npx", "bunx"):
        return _direct([w for w in rest if not w.startswith("-")]), False
    if tool in ("pnpm", "yarn") and rest[:1] in (["exec"], ["dlx"]):
        return _direct(rest[1:]), False
    if tool in ("jest", "vitest") or (tool == "playwright" and rest[:1] == ["test"]):
        return _direct(words), False
    if tool in ("npm", "pnpm", "yarn", "bun"):
        args = [w for w in rest if not w.startswith("-")]
        script = args[1] if args[:1] == ["run"] and len(args) > 1 else (args[0] if args else "")
        if tool in ("pnpm", "yarn") and script in ("jest", "vitest"):
            return script, False
        body = _package_scripts(cwd).get(script or "test", "")
        for name in ("vitest", "jest"):
            if re.search(rf"(^|[\s;&|]){name}\b", body):
                return name, True
        if re.search(r"(^|[\s;&|])playwright\s+test\b", body):
            return "playwright", True
    return "", False


def _direct(words: list[str]) -> str:
    if not words:
        return ""
    name = Path(words[0]).name
    if name in ("jest", "vitest"):
        return name
    if name == "playwright" and words[1:2] == ["test"]:
        return "playwright"
    return ""


def prepare(argv: list[str], *, cwd: Path, out_dir: Path) -> Wrapped:
    """*argv* changed so it also writes results into *out_dir* (an absolute folder)."""
    if len(argv) == 1 and re.search(r"[\s;&|<>$`]", argv[0]):
        return Wrapped("", "your command", ["sh", "-c", argv[0]], shell=True)
    argv = list(argv)
    i = _runner_word(argv)
    words = argv[i:]
    if not words:
        return Wrapped("", "your command", argv)
    name = Path(words[0]).name
    out = out_dir

    # Python launchers: `uv run pytest`, `poetry run pytest`, `python -m pytest`.
    py = words
    if name in ("uv", "poetry", "pipenv", "hatch", "pdm") and words[1:2] == ["run"]:
        py = words[2:]
    if py and Path(py[0]).name.startswith("python") and py[1:3] == ["-m", "pytest"]:
        py = py[2:]
    if py and Path(py[0]).name in ("pytest", "py.test"):
        if _has(argv, "--junitxml", "--junit-xml"):
            return Wrapped("pytest", "pytest", argv, globs=_flag_value(argv, "--junitxml"))
        target = out / "pytest.xml"
        return Wrapped("pytest", "pytest", [*argv, f"--junitxml={target}"], files=[target])

    node, via_script = _node_runner(words, cwd)
    sep = ["--"] if via_script and name == "npm" else []
    if node == "jest":
        if _has(argv, "--outputFile"):
            return Wrapped("jest", "Jest", argv, globs=_flag_value(argv, "--outputFile"))
        target = out / "jest.json"
        return Wrapped(
            "jest", "Jest", [*argv, *sep, "--json", f"--outputFile={target}"], files=[target]
        )
    if node == "vitest":
        target = out / "vitest.json"
        extra = ["--reporter=default", "--reporter=json", f"--outputFile.json={target}"]
        return Wrapped("vitest", "Vitest", [*argv, *sep, *extra], files=[target])
    if node == "playwright":
        target = out / "playwright.json"
        return Wrapped(
            "playwright",
            "Playwright",
            [*argv, *sep, "--reporter=list,json"],
            env={"PLAYWRIGHT_JSON_OUTPUT_NAME": str(target)},
            files=[target],
            tier="e2e",
        )

    if name == "go" and words[1:2] == ["test"]:
        target = out / "go-test.json"
        new = list(argv)
        if "-json" not in words:
            new.insert(i + 2, "-json")
        return Wrapped("go", "go test", new, files=[target], go_json=True)
    if name == "dotnet" and words[1:2] == ["test"]:
        if _has(argv, "--logger", "-l"):
            return Wrapped("dotnet", "dotnet test", argv, globs=["**/*.trx"])
        folder = out / "dotnet"
        return Wrapped(
            "dotnet",
            "dotnet test",
            [*argv, "--logger", "trx", "--results-directory", str(folder)],
            globs=[f"{folder.as_posix()}/*.trx"],
        )
    if name in ("mvn", "mvnw"):
        return Wrapped("maven", "Maven", argv, globs=["**/target/*-reports/TEST-*.xml"])
    if name in ("gradle", "gradlew"):
        return Wrapped("gradle", "Gradle", argv, globs=["**/build/test-results/**/TEST-*.xml"])
    if name == "rspec" or (name == "bundle" and "rspec" in words[1:3]):
        target = out / "rspec.json"
        extra = ["--format", "progress", "--format", "json", "--out", str(target)]
        return Wrapped("rspec", "RSpec", [*argv, *extra], files=[target])
    if name == "phpunit":
        target = out / "phpunit.xml"
        return Wrapped("phpunit", "PHPUnit", [*argv, "--log-junit", str(target)], files=[target])
    return Wrapped("", "your command", argv)


def _flag_value(argv: list[str], flag: str) -> list[str]:
    for n, arg in enumerate(argv):
        if arg.startswith(flag + "="):
            return [arg.split("=", 1)[1]]
        if arg == flag and n + 1 < len(argv):
            return [argv[n + 1]]
    return []


def run(wrapped: Wrapped, *, cwd: Path, env: dict[str, str]) -> int:
    """Run the command in *cwd*; its output goes to this terminal. Returns its exit code."""
    full_env = {**env, **wrapped.env}
    if (
        wrapped.argv
        and not wrapped.shell
        and shutil.which(wrapped.argv[0], path=full_env.get("PATH")) is None
    ):
        local = cwd / wrapped.argv[0]
        if not local.is_file():
            print(f"qasight: command not found: {wrapped.argv[0]}", file=sys.stderr)
            return 127
    if not wrapped.go_json:
        return subprocess.run(wrapped.argv, cwd=cwd, env=full_env, check=False).returncode  # noqa: S603
    target = wrapped.files[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        target.open("w", encoding="utf-8") as sink,
        subprocess.Popen(  # noqa: S603
            wrapped.argv, cwd=cwd, env=full_env, stdout=subprocess.PIPE, text=True
        ) as proc,
    ):
        assert proc.stdout is not None
        for line in proc.stdout:
            sink.write(line)
            _echo_go_line(line, sys.stdout.write)
    return proc.returncode


def _echo_go_line(line: str, write: Callable[[str], object]) -> None:
    """Print ``go test -json`` output as the plain text ``go test`` would have printed."""
    try:
        event = json.loads(line)
    except ValueError:
        write(line)
        return
    if isinstance(event, dict) and event.get("Action") == "output":
        write(str(event.get("Output", "")))


def result_files(wrapped: Wrapped, *, cwd: Path, since: float) -> list[Path]:
    """The result files the run left: the ones asked for, else any written while it ran."""
    found = [p for p in wrapped.files if p.is_file() and p.stat().st_size > 0]
    for pattern in wrapped.globs:
        base = Path(pattern)
        if base.is_absolute():
            matches = list(Path(base.anchor).glob(str(base.relative_to(base.anchor))))
        else:
            matches = list(cwd.glob(pattern))
        found += [p for p in matches if p.is_file() and p.stat().st_mtime >= since]
    if found or wrapped.files or wrapped.globs:
        return sorted(dict.fromkeys(p.resolve() for p in found))
    from qasight.services.project_setup import find_result_files

    return [p for p in find_result_files(cwd, include_tracked=True) if p.stat().st_mtime >= since]


def new_out_dir(cwd: Path) -> Path:
    """A fresh folder under *cwd* for this run's result files (``.testo/wrap/<time>``)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = cwd / ".testo" / "wrap" / stamp
    n = 2
    while folder.exists():
        folder = cwd / ".testo" / "wrap" / f"{stamp}-{n}"
        n += 1
    return folder
