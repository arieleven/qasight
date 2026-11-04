"""Ask each proposed stage's runner which tests it would run, without running them.

``qasight init --check`` uses this so a proposed stage comes with proof ("42
tests") instead of a guess from project files. Each runner has a listing mode
(``pytest --collect-only``, ``jest --listTests``, ``go test -list``...). Listing
still loads the project's test code (pytest imports ``conftest.py``, Go and
.NET compile), which is why it is opt-in. Node runners run from the project's
own ``node_modules/.bin``, so nothing is downloaded. Maven and Gradle have no
listing mode that doesn't run the tests, so they are skipped.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from qasight.services.project_setup import Stack

DEFAULT_TIMEOUT_S = 180.0
_TAIL = 300


@dataclass(frozen=True)
class StageCheck:
    """What a runner said about one stage."""

    status: str  # ok, empty, error, missing, timeout, skipped
    count: int | None = None
    unit: str = "tests"
    command: str = ""
    detail: str = ""

    @property
    def summary(self) -> str:
        if self.status == "ok":
            unit = self.unit[:-1] if self.count == 1 and self.unit.endswith("s") else self.unit
            return f"{self.count} {unit}"
        if self.status == "empty":
            return f"no {self.unit} found"
        if self.status in ("missing", "skipped"):
            return f"not checked: {self.detail}"
        if self.status == "timeout":
            return f"listing timed out ({self.detail})"
        counted = f"{self.count} {self.unit}, " if self.count else ""
        return f"{counted}listing failed: {self.detail}"


def _up_to_root(folder: Path, root: Path) -> list[Path]:
    """*folder*, its parents up to *root*, then *root* (a workspace installs at the top)."""
    out = [folder, *(p for p in folder.parents if p == root or root in p.parents)]
    return out if root in out else [*out, root]


def _python(folder: Path, root: Path) -> str | None:
    for base in _up_to_root(folder, root):
        for rel in (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe"):
            if (base / rel).is_file():
                return str(base / rel)
    if importlib.util.find_spec("pytest") is not None:
        return sys.executable  # qasight's own Python has pytest (e.g. `pip install qasight pytest`)
    return shutil.which("python3") or shutil.which("python")


def _node_bin(name: str, folder: Path, root: Path) -> str:
    """The project's own copy of a Node runner, so nothing is downloaded."""
    for base in _up_to_root(folder, root):
        candidate = base / "node_modules" / ".bin" / name
        if candidate.is_file():
            return str(candidate)
    return f"node_modules/.bin/{name}"  # reported as not installed


def _pytest_paths(stack: Stack) -> list[str]:
    args = stack.stage.get("args") or []
    return [str(a) for a in args if not str(a).startswith("-")]


def list_command(stack: Stack, *, folder: Path, root: Path) -> list[str] | None:
    """The argv that lists *stack*'s tests in *folder*, or ``None`` when the runner can't."""
    key = stack.key
    if key == "pytest":
        python = _python(folder, root)
        return [python or "python", "-m", "pytest", "--collect-only", "-q", *_pytest_paths(stack)]
    if key == "jest":
        return [_node_bin("jest", folder, root), "--listTests"]
    if key == "vitest":
        return [_node_bin("vitest", folder, root), "list"]
    if key == "playwright":
        return [_node_bin("playwright", folder, root), "test", "--list"]
    if key == "go":
        return ["go", "test", "-list", ".", "./..."]
    if key == "dotnet":
        return ["dotnet", "test", "--list-tests"]
    if key == "rspec":
        return ["bundle", "exec", "rspec", "--dry-run", "--format", "json"]
    if key == "phpunit":
        return ["vendor/bin/phpunit", "--list-tests"]
    return None


def count_listed(key: str, output: str) -> tuple[int | None, str]:
    """How many tests (or test files) a listing printed, and what it counted."""
    lines = [line.rstrip() for line in output.splitlines()]
    if key == "pytest":
        match = re.search(r"(\d+)(?:/\d+)? tests? collected", output)
        if match:
            return int(match.group(1)), "tests"
        if "no tests collected" in output or "no tests ran" in output:
            return 0, "tests"
        return sum(1 for line in lines if "::" in line), "tests"
    if key == "jest":
        return sum(1 for line in lines if re.search(r"\.[cm]?[jt]sx?$", line)), "test files"
    if key == "vitest":
        return sum(1 for line in lines if line.strip()), "tests"
    if key == "playwright":
        match = re.search(r"Total: (\d+) tests?", output)
        return (int(match.group(1)) if match else 0), "tests"
    if key == "go":
        return sum(1 for line in lines if re.fullmatch(r"(Test|Example|Fuzz)\w*", line)), "tests"
    if key == "dotnet":
        _, _, after = output.partition("The following Tests are available:")
        return sum(1 for line in after.splitlines() if line.startswith("    ")), "tests"
    if key == "rspec":
        start = output.find("{")
        try:
            data = json.loads(output[start:]) if start >= 0 else {}
        except json.JSONDecodeError:
            match = re.search(r'"example_count"\s*:\s*(\d+)', output)
            return (int(match.group(1)) if match else None), "tests"
        return int((data.get("summary") or {}).get("example_count") or 0), "tests"
    if key == "phpunit":
        return sum(1 for line in lines if re.match(r"\s*- \S", line)), "tests"
    return None, "tests"


def _last_line(text: str) -> str:
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return line.strip()[:_TAIL]
    return ""


def check_stage(stack: Stack, *, root: Path, timeout_s: float = DEFAULT_TIMEOUT_S) -> StageCheck:
    """List *stack*'s tests from its ``target_repo`` under *root*. Never runs a test."""
    folder = (root / str(stack.stage.get("target_repo") or ".")).resolve()
    argv = list_command(stack, folder=folder, root=root)
    if argv is None:
        return StageCheck(
            status="skipped", detail=f"{stack.label} can't list tests without running them"
        )
    tool = argv[0]
    present = (folder / tool).is_file() if "/" in tool else shutil.which(tool) is not None
    if not present:
        hint = " (install the project's dependencies first)" if "/" in tool else ""
        return StageCheck(status="missing", detail=f"{Path(tool).name} isn't installed{hint}")
    if stack.key == "pytest":
        shown = shlex.join(["python", *argv[1:]])
    else:
        shown = shlex.join(
            [Path(argv[0]).name if "node_modules" in argv[0] else argv[0], *argv[1:]]
        )
    env = {**os.environ, "CI": "1", "NO_COLOR": "1", "FORCE_COLOR": "0"}
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv per runner, no shell
            argv,
            cwd=folder,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return StageCheck(status="timeout", command=shown, detail=f"{timeout_s:g}s")
    except OSError as exc:
        return StageCheck(status="missing", command=shown, detail=str(exc)[:_TAIL])
    # Every listing goes to stdout; stderr holds warnings and, on failure, the reason.
    count, unit = count_listed(stack.key, proc.stdout)
    # pytest exits 5 when it collects nothing; that's an answer, not a failure.
    failed = proc.returncode != 0 and not (stack.key == "pytest" and proc.returncode == 5)
    if failed and "No module named pytest" in proc.stderr:
        return StageCheck(
            status="missing", command=shown, detail=f"pytest isn't installed for {tool}"
        )
    if failed:
        detail = _last_line(proc.stderr) or _last_line(proc.stdout) or f"exit {proc.returncode}"
        return StageCheck(status="error", count=count, unit=unit, command=shown, detail=detail)
    if not count:
        return StageCheck(status="empty", count=0, unit=unit, command=shown)
    return StageCheck(status="ok", count=count, unit=unit, command=shown)
