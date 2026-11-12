"""Run the scorecard tools on a repository: a local folder or a git URL (cloned shallow).

``qasight scorecard scan`` and the UI's "Scan a repository" both come here.
The scan picks the switched-on tools that fit the repository's languages (and
the app tools when it has an app URL), runs the ones installed on this machine,
reads each report with its parser and hands the result to ``record``. It never
installs anything: a missing tool is listed as skipped with its install hint.

A tool's exit code is not its verdict (most exit 1 when they find something),
so a tool counts as failed only when it left no report its parser can read.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qasight.services.scorecard.ignore import (
    IgnoreRule,
    apply_ignores,
    find_ignore_file,
    load_ignores,
)
from qasight.services.scorecard.parsers import ReportError, ToolResult
from qasight.services.scorecard.tools import TOOLS, TOOLS_BY_ID, ScanContext, Tool

DEFAULT_TOOL_TIMEOUT_S = 900
CLONE_TIMEOUT_S = 1800
# Files looked at to guess the languages; enough for the Linux kernel's top folders.
_DETECT_LIMIT = 20000
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox"}
_URL = re.compile(r"^(https?://|ssh://|git@)[^\s]+$")
_SHORT_GITHUB = re.compile(r"^(github\.com|gitlab\.com)/[\w.-]+/[\w.-]+/?$")

Event = dict[str, Any]
NOT_INSTALLED = "not installed"


class ScanError(ValueError):
    """The target can't be scanned (a bad path or URL, a failed clone)."""


@dataclass
class ScanTarget:
    repo: Path
    project: str
    version: str
    commit_sha: str | None
    github_repo: str | None
    # A temporary clone to delete when the scan is done.
    cleanup: Path | None = None


@dataclass
class ScanOutcome:
    target: ScanTarget
    out_dir: Path
    languages: set[str]
    results: dict[str, ToolResult] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    # Tools that fit the repository (its languages, switched on, an app URL when they need
    # one): the ones planned plus the ones not installed here. The scorecard's coverage.
    expected: list[str] = field(default_factory=list)
    ignore_file: Path | None = None

    def not_run(self) -> dict[str, str]:
        """Every tool that gave no result and why, each once: skipped first, then failed."""
        out = dict(self.skipped)
        for tool_id, message in self.errors.items():
            out[tool_id] = f"failed: {message}"
        return out

    def missing(self) -> dict[str, str]:
        """The expected tools that gave no result, and why."""
        reasons = self.not_run()
        return {t: reasons.get(t, "no result") for t in self.expected if t not in self.results}


def is_git_url(target: str) -> bool:
    return bool(_URL.match(target.strip()) or _SHORT_GITHUB.match(target.strip()))


def _normalize_url(target: str) -> str:
    target = target.strip()
    return f"https://{target}" if _SHORT_GITHUB.match(target) else target


def _owner_repo(url: str) -> tuple[str | None, str | None]:
    """("owner/name", host) from a git URL, or (None, None)."""
    if url.startswith("git@"):
        host, _, path = url[4:].partition(":")
    else:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    segments = [s for s in path.strip("/").removesuffix(".git").split("/") if s]
    if len(segments) < 2:
        return (segments[-1] if segments else None), host or None
    return "/".join(segments[-2:]), host or None


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def resolve_target(
    target: str,
    *,
    project: str | None = None,
    version: str | None = None,
    ref: str | None = None,
    workdir: Path | None = None,
    on_event: Callable[[Event], None] | None = None,
) -> ScanTarget:
    """A local folder as is, or a git URL cloned (``--depth 1``) into ``workdir`` or a temp folder."""
    target = target.strip()
    if not target or target.startswith("-"):
        raise ScanError("give a folder or a git URL to scan")
    cleanup: Path | None = None
    github_repo: str | None = None
    if is_git_url(target):
        url = _normalize_url(target)
        name, host = _owner_repo(url)
        if host and host.endswith("github.com") and name and "/" in name:
            github_repo = name
        base = workdir or Path(tempfile.mkdtemp(prefix="qasight-scan-"))
        if workdir is None:
            cleanup = base
        repo = base / (name or "repo").replace("/", "__")
        if on_event:
            on_event({"type": "clone", "message": f"Cloning {url}"})
        argv = ["git", "-c", "protocol.ext.allow=never", "clone", "--depth", "1", "--quiet"]
        if ref:
            argv += ["--branch", ref]
        argv += ["--", url, str(repo)]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=CLONE_TIMEOUT_S,
                check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            raise ScanError(f"cloning {url} took over {CLONE_TIMEOUT_S // 60} minutes") from exc
        except OSError as exc:
            raise ScanError(f"git is needed to clone {url}: {exc}") from exc
        if proc.returncode != 0:
            raise ScanError(f"could not clone {url}: {(proc.stderr or proc.stdout).strip()[-400:]}")
        default_project = name or repo.name
    else:
        repo = Path(target).expanduser().resolve()
        if not repo.is_dir():
            raise ScanError(f"{target} is not a folder (or a git URL)")
        remote = _git(repo, "remote", "get-url", "origin")
        name, host = _owner_repo(remote) if remote else (None, None)
        if remote and host and host.endswith("github.com") and name and "/" in name:
            github_repo = name
        default_project = name or repo.name
    commit = _git(repo, "rev-parse", "HEAD")
    if not version:
        version = _git(repo, "describe", "--tags", "--always") or (
            commit[:12] if commit else "local"
        )
    return ScanTarget(
        repo=repo,
        project=(project or default_project).strip(),
        version=version.strip(),
        commit_sha=commit,
        github_repo=github_repo,
        cleanup=cleanup,
    )


def detect_languages(repo: Path) -> set[str]:
    """The languages the scan's tools care about: python, node, c, github-actions."""
    found: set[str] = set()
    if (repo / ".github" / "workflows").is_dir():
        found.add("github-actions")
    if (repo / "package.json").is_file():
        found.add("node")
    if any(
        (repo / f).is_file()
        for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
    ):
        found.add("python")
    seen = 0
    for _root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            seen += 1
            suffix = Path(name).suffix.lower()
            if suffix == ".py":
                found.add("python")
            elif suffix in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp"}:
                found.add("c")
            elif suffix in {".js", ".ts", ".tsx", ".jsx"} and (repo / "package.json").is_file():
                found.add("node")
        if seen >= _DETECT_LIMIT or {"python", "c", "node"} <= found:
            break
    return found


def plan_tools(
    *,
    languages: set[str],
    enabled: dict[str, bool],
    only: list[str] | None = None,
    url: str | None = None,
    openapi: str | None = None,
) -> tuple[list[Tool], dict[str, str]]:
    """The tools to run, and why each other tool is left out."""
    run: list[Tool] = []
    skipped: dict[str, str] = {}
    wanted = set(only) if only else None
    for tool in TOOLS:
        if wanted is not None and tool.id not in wanted:
            continue
        if wanted is None and not enabled.get(tool.id, tool.default_on):
            skipped[tool.id] = "switched off"
            continue
        if tool.kind == "import" or tool.command is None:
            if wanted is not None:
                skipped[tool.id] = "import only: run it yourself, then `qasight scorecard import`"
            continue
        if tool.kind == "app" and not (url or (tool.id == "schemathesis" and openapi)):
            skipped[tool.id] = "needs a running app (--url)"
            continue
        if tool.languages and not tool.languages & languages:
            skipped[tool.id] = f"no {'/'.join(sorted(tool.languages))} code found"
            continue
        if not tool.installed():
            skipped[tool.id] = f"{NOT_INSTALLED} ({tool.install})"
            continue
        run.append(tool)
    return run, skipped


def run_tool(
    tool: Tool, ctx: ScanContext, *, timeout_s: int = DEFAULT_TOOL_TIMEOUT_S
) -> ToolResult:
    """Run one tool and parse its report; raises :class:`ReportError` or :class:`LookupError`."""
    assert tool.command is not None
    cmd = tool.command(ctx)
    if cmd is None:
        raise LookupError("nothing for it to check in this repository")
    for path, text in cmd.files.items():
        path.write_text(text, encoding="utf-8")
    ctx.out.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            cmd.argv,
            cwd=cmd.cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReportError(f"stopped after {timeout_s} s") from exc
    except OSError as exc:
        raise ReportError(f"could not start {cmd.argv[0]}: {exc}") from exc
    if cmd.stdout_is_report:
        ctx.out.write_text(proc.stdout, encoding="utf-8")
    (ctx.out.parent / f"{tool.id}.log").write_text(
        f"$ {' '.join(cmd.argv)}\nexit {proc.returncode}\n\n{proc.stderr}", encoding="utf-8"
    )
    if not ctx.out.is_file() or ctx.out.stat().st_size == 0:
        raise ReportError(_failure(tool, proc.returncode, proc.stderr or proc.stdout or ""))
    return tool.parse(ctx.out)


# Lines in a tool's output that say why it stopped.
_REASON = re.compile(r"error|fail|could not|cannot|can't|unable|denied|not found|timed? ?out", re.I)


def _failure(tool: Tool, code: int, output: str) -> str:
    """Why a tool left no report: its exit code, the lines of its output that say why, and a hint."""
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    reasons = [line for line in lines if _REASON.search(line)][-3:] or lines[-3:]
    message = f"exited {code} without a report"
    if reasons:
        message += f": {' | '.join(reasons)[:400]}"
    if tool.failure_hint:
        message += f". {tool.failure_hint}"
    return message


def run_scan(
    target: ScanTarget,
    *,
    out_dir: Path,
    enabled: dict[str, bool],
    record: Callable[[str, ToolResult], None],
    only: list[str] | None = None,
    url: str | None = None,
    openapi: str | None = None,
    timeout_s: int = DEFAULT_TOOL_TIMEOUT_S,
    on_event: Callable[[Event], None] | None = None,
    ignore_file: Path | None = None,
    record_coverage: Callable[[ScanOutcome], None] | None = None,
) -> ScanOutcome:
    """Scan ``target`` with every tool that fits; ``record`` stores each result as it comes.

    Findings accepted in ``ignore_file`` (default: the repository's
    ``scorecard-ignore.yml``) are left out before a result is recorded, and
    ``record_coverage`` gets the outcome at the end, to keep which tools ran.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    ignore_path = ignore_file or find_ignore_file(target.repo)
    ignores: list[IgnoreRule] = load_ignores(ignore_path) if ignore_path else []
    languages = detect_languages(target.repo)
    tools, skipped = plan_tools(
        languages=languages, enabled=enabled, only=only, url=url, openapi=openapi
    )
    outcome = ScanOutcome(
        target=target,
        out_dir=out_dir,
        languages=languages,
        skipped=dict(skipped),
        expected=[t.id for t in tools]
        + [t for t, why in skipped.items() if why.startswith(NOT_INSTALLED)],
        ignore_file=ignore_path,
    )
    emit = on_event or (lambda _e: None)
    emit(
        {
            "type": "plan",
            "tools": [t.id for t in tools],
            "skipped": skipped,
            "languages": sorted(languages),
        }
    )
    for tool in tools:
        tool_dir = out_dir / tool.id
        tool_dir.mkdir(parents=True, exist_ok=True)
        ctx = ScanContext(
            repo=target.repo,
            out=tool_dir / tool.report_name,
            url=url,
            openapi=openapi,
            github_repo=target.github_repo,
        )
        emit({"type": "tool", "tool": tool.id, "status": "running"})
        started = time.monotonic()
        try:
            result = run_tool(tool, ctx, timeout_s=timeout_s)
        except LookupError as exc:
            outcome.skipped[tool.id] = str(exc)
            # Nothing for it in this repository: it doesn't count against the coverage.
            outcome.expected.remove(tool.id)
            emit({"type": "tool", "tool": tool.id, "status": "skipped", "message": str(exc)})
            continue
        except ReportError as exc:
            outcome.errors[tool.id] = str(exc)
            emit({"type": "tool", "tool": tool.id, "status": "error", "message": str(exc)})
            continue
        finally:
            outcome.seconds[tool.id] = round(time.monotonic() - started, 1)
        result = apply_ignores(tool.id, result, ignores)
        record(tool.id, result)
        outcome.results[tool.id] = result
        emit(
            {
                "type": "tool",
                "tool": tool.id,
                "status": "done",
                "score": result.score,
                "message": result.summary,
            }
        )
    if record_coverage is not None:
        record_coverage(outcome)
    return outcome


def finish(target: ScanTarget) -> None:
    """Delete the temporary clone, if the scan made one."""
    if target.cleanup is not None:
        shutil.rmtree(target.cleanup, ignore_errors=True)


def known_tool_ids(raw: str | None) -> list[str] | None:
    """``--tools bandit,semgrep`` as ids; raises :class:`ScanError` for an unknown one."""
    if not raw:
        return None
    ids = [t.strip() for t in raw.split(",") if t.strip()]
    unknown = [t for t in ids if t not in TOOLS_BY_ID]
    if unknown:
        raise ScanError(f"unknown tool(s): {', '.join(unknown)}; known: {', '.join(TOOLS_BY_ID)}")
    return ids
