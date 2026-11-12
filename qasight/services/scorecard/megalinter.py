"""MegaLinter as the scorecard's engine: it picks the linters and scanners that fit any repository.

`MegaLinter <https://megalinter.io>`_ (open source, by OX Security) ships 100+
linters and scanners for ~65 languages in Docker images and turns on the ones
whose files it finds. QASight runs it once, reads its JSON report and each
linter's SARIF, and records one result per linter that ran, so a Java or Ruby
repository is checked by Java and Ruby tools without QASight knowing either.

The repository's own ``.mega-linter.yml`` and linter configs are used as they
are; QASight only sets where the reports go and turns off fixing files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qasight.services.scorecard.parsers import (
    Finding,
    ReportError,
    ToolResult,
    _sarif_severity,
    _short,
    from_findings,
)
from qasight.services.scorecard.paths import lint_exclude_regex

ENGINE_ID = "megalinter"
PREFIX = f"{ENGINE_ID}:"
IMAGE_TAG = "v9"
DEFAULT_TIMEOUT_S = 3600

# Main language -> MegaLinter flavor (a smaller image with that language's linters
# and the repository-wide scanners). Anything else uses the full image.
FLAVORS: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "javascript",
    "java": "java",
    "kotlin": "java",
    "groovy": "java",
    "go": "go",
    "rust": "rust",
    "ruby": "ruby",
    "php": "php",
    "csharp": "dotnet",
    "vbdotnet": "dotnet",
    "fsharp": "dotnet",
    "terraform": "terraform",
    "c": "c_cpp",
    "cpp": "c_cpp",
    "swift": "swift",
}

# Linters that only list what is in the repository (an SBOM), not findings.
_NO_FINDINGS = {"REPOSITORY_SYFT", "REPOSITORY_TRIVY_SBOM", "REPOSITORY_GIT_DIFF"}
# Off unless the repository's own MegaLinter config turns it on: TruffleHog verifies what it
# finds by logging in with it, which sends a found secret to its provider (gitleaks and
# secretlint find the same secrets offline).
_OFF_BY_DEFAULT = {"REPOSITORY_TRUFFLEHOG"}
# Settings in pyproject.toml that MegaLinter doesn't look for on its own.
_PYPROJECT_SECTIONS = {
    "PYTHON_RUFF": "ruff",
    "PYTHON_MYPY": "mypy",
    "PYTHON_PYLINT": "pylint",
    "PYTHON_ISORT": "isort",
}
# Secrets, static security analysis, misconfiguration.
_SECURITY = re.compile(
    r"gitleaks|secretlint|trufflehog|devskim|semgrep|bandit|checkov|kics|tfsec|terrascan|"
    r"kubescape|gosec|brakeman|snyk|codeql",
    re.I,
)
# Known vulnerable dependencies.
_SUPPLY_CHAIN = re.compile(r"grype|dustilock|osv|audit|dependency|trivy$", re.I)
_SPELL = re.compile(r"^SPELL_", re.I)

# Error lines from a linter that could not do its job (as opposed to finding problems).
_TOOL_FAILURE = re.compile(
    r"no such host|could not resolve|connection refused|network is unreachable|timed out|"
    r"failed to (fetch|download|load)|database does not exist|unable to download|Traceback",
    re.I,
)


@dataclass
class LinterRun:
    key: str
    name: str
    category: str
    result: ToolResult | None = None
    error: str | None = None
    # True when it ran with the repository's own settings, not MegaLinter's defaults.
    configured: bool = False
    # False for linters MegaLinter runs as non-blocking (formatters, mostly).
    blocking: bool = True


@dataclass
class EngineOutcome:
    image: str
    runs: list[LinterRun] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None


def docker() -> str | None:
    """The container command on this machine (Docker, else Podman), or None."""
    return shutil.which("docker") or shutil.which("podman")


def image_for(main_language: str | None, override: str | None = None) -> str:
    if override:
        return override
    flavor = FLAVORS.get(main_language or "")
    # GitHub Container Registry: no Docker Hub pull limits.
    name = "ghcr.io/oxsecurity/megalinter" + (f"-{flavor}" if flavor else "")
    return f"{name}:{IMAGE_TAG}"


def category_of(key: str, linter_name: str) -> str | None:
    """The scorecard category of a MegaLinter linter, or None when it reports no findings to score."""
    if key in _NO_FINDINGS or _SPELL.match(key):
        return None
    probe = f"{key} {linter_name}"
    if key == "REPOSITORY_TRIVY" or _SUPPLY_CHAIN.search(linter_name):
        return "supply-chain"
    if _SECURITY.search(probe):
        return "security"
    return "code"


def display_name(key: str, linter_name: str) -> str:
    descriptor = key.split("_", 1)[0]
    pretty = {
        "REPOSITORY": "repository",
        "COPYPASTE": "copy-paste",
        "CSHARP": "C#",
        "JAVASCRIPT": "JavaScript",
        "TYPESCRIPT": "TypeScript",
        "JSON": "JSON",
        "YAML": "YAML",
        "XML": "XML",
        "HTML": "HTML",
        "CSS": "CSS",
        "SQL": "SQL",
        "PHP": "PHP",
        "API": "API",
        "BASH": "Bash",
    }.get(descriptor, descriptor.replace("_", " ").title())
    return f"{linter_name} ({pretty})"


def name_from_id(tool_id: str) -> str:
    """``megalinter:REPOSITORY_GRYPE`` -> ``grype (repository)``."""
    key = tool_id.removeprefix(PREFIX)
    linter = key.split("_", 1)[1].lower().replace("_", "-") if "_" in key else key.lower()
    return display_name(key, linter)


def _proxy_args() -> tuple[list[str], dict[str, str]]:
    """Docker args and env so the linters inside reach the network the way this machine does."""
    args: list[str] = []
    env: dict[str, str] = {}
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"):
        if os.environ.get(var):
            env[var] = os.environ[var]
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or ""
    host = urlsplit(proxy).hostname if proxy else None
    if host in {"127.0.0.1", "localhost", "::1"}:
        # A proxy on this machine's loopback is only reachable from the host network.
        args += ["--network", "host"]
    ca = next(
        (
            os.environ[v]
            for v in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")
            if os.environ.get(v) and Path(os.environ[v]).is_file()
        ),
        None,
    )
    if ca:
        args += ["-v", f"{ca}:/etc/qasight/ca.crt:ro"]
        for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO"):
            env[var] = "/etc/qasight/ca.crt"
    return args, env


def project_configs(repo: Path) -> dict[str, str]:
    """``<KEY>_CONFIG_FILE`` settings that point linters at the project's pyproject.toml."""
    try:
        text = (repo / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {
        f"{key}_CONFIG_FILE": "pyproject.toml"
        for key, section in _PYPROJECT_SECTIONS.items()
        if re.search(rf"^\[tool\.{section}[.\]]", text, re.M)
    }


def command(
    repo: Path,
    out: Path,
    *,
    image: str,
    disabled: list[str] | None = None,
) -> list[str]:
    """The ``docker run`` for one full scan of ``repo``, reports into ``out``."""
    engine = docker() or "docker"
    args, env = _proxy_args()
    env.update(
        {
            "DEFAULT_WORKSPACE": "/tmp/lint",
            "REPORT_OUTPUT_FOLDER": "/tmp/reports",
            "VALIDATE_ALL_CODEBASE": "true",
            "APPLY_FIXES": "none",
            "SARIF_REPORTER": "true",
            "JSON_REPORTER": "true",
            "GITHUB_COMMENT_REPORTER": "false",
            "GITHUB_STATUS_REPORTER": "false",
            "GITLAB_COMMENT_REPORTER": "false",
            "UPDATED_SOURCES_REPORTER": "false",
            "FLAVOR_SUGGESTIONS": "false",
            "PRINT_ALPACA": "false",
            "LOG_LEVEL": "INFO",
        }
    )
    has_own_config = any(
        (repo / name).is_file() for name in (".mega-linter.yml", ".mega-linter.yaml")
    )
    off = list(disabled or [])
    if not has_own_config:
        # Spell and link checkers flood a scan with words they don't know and need the network.
        env["DISABLE"] = "SPELL"
        off += sorted((_NO_FINDINGS - {"REPOSITORY_GIT_DIFF"}) | _OFF_BY_DEFAULT)
        # Lint source only: the score leaves tests, vendored code and docs out anyway.
        env["FILTER_REGEX_EXCLUDE"] = lint_exclude_regex()
        env.update(project_configs(repo))
    if off:
        env["DISABLE_LINTERS"] = ",".join(sorted(set(off)))
    argv = [engine, "run", "--rm", *args]
    argv += ["-v", f"{repo}:/tmp/lint", "-v", f"{out}:/tmp/reports"]
    for key, value in sorted(env.items()):
        argv += ["-e", f"{key}={value}"]
    argv.append(image)
    return argv


def _sarif_findings(path: Path) -> list[Finding] | None:
    """The findings in one linter's SARIF file, or None when it isn't readable SARIF."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return None
    findings: list[Finding] = []
    for run in data["runs"]:
        rules: dict[str, dict[str, Any]] = {}
        driver = ((run or {}).get("tool") or {}).get("driver") or {}
        for rule in driver.get("rules") or []:
            if isinstance(rule, dict) and rule.get("id"):
                rules[str(rule["id"])] = rule
        for result in (run or {}).get("results") or []:
            if not isinstance(result, dict):
                continue
            rule_id = str(result.get("ruleId") or "")
            rule = rules.get(rule_id, {})
            level = (
                result.get("level")
                or (rule.get("defaultConfiguration") or {}).get("level")
                or "warning"
            )
            props = {**(rule.get("properties") or {}), **(result.get("properties") or {})}
            location = ""
            locs = result.get("locations") or []
            if locs:
                phys = (locs[0] or {}).get("physicalLocation") or {}
                uri = str((phys.get("artifactLocation") or {}).get("uri") or "")
                uri = re.sub(r"^(file://)?/tmp/lint/", "", uri)
                line = (phys.get("region") or {}).get("startLine")
                location = f"{uri}:{line}" if uri and line else uri
            message = (result.get("message") or {}).get("text") or ""
            findings.append(
                Finding(
                    _lint_severity(level, props),
                    rule_id or _short(message, 80) or "finding",
                    _short(message),
                    location,
                )
            )
    return findings


def _lint_severity(level: Any, props: dict[str, Any]) -> str:
    """A linter's SARIF level as a severity: a lint error is a low issue, a warning an info one.

    Like SonarQube's code smells (minor and info), so style counts but never outweighs a
    vulnerability. Rules that carry a ``security-severity`` keep it.
    """
    if props.get("security-severity") is not None:
        return _sarif_severity(level, props)
    return {"error": "low", "warning": "info", "note": "info", "none": "info"}.get(
        str(level).lower(), "info"
    )


def _log_tail(out: Path, key: str) -> str:
    for log in sorted((out / "linters_logs").glob(f"{key}-*.log")):
        lines = [
            line
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        hits = [line for line in lines if _TOOL_FAILURE.search(line)]
        return _short(" ".join((hits or lines)[-2:]), 300)
    return ""


def _rules_configs(out: Path) -> dict[str, str]:
    """Each linter's settings file as MegaLinter's log names it (``/x`` is the repository's)."""
    log = out / "megalinter.log"
    try:
        text = re.sub(r"\x1b\[[0-9;]*m", "", log.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}
    found: dict[str, str] = {}
    for match in re.finditer(
        r"MegaLinter key: \[([A-Z0-9_]+)\]\s*\n(?:.*\n){0,3}?- Rules config: (.+)", text
    ):
        found.setdefault(match.group(1), match.group(2).strip())
    return found


def _set_advisory(runs: list[LinterRun]) -> None:
    """Mark linters that duplicate the project's own choice for a language as advisory.

    When a project configures a linter for a language (its ruff settings in pyproject.toml,
    its .eslintrc), the others MegaLinter runs on that language use MegaLinter's defaults:
    flake8's 79-character lines in a project whose ruff allows 100. Like Codacy, which
    follows a repository's config files, their issues are shown but not scored.
    """
    owners: dict[str, list[str]] = {}
    for r in runs:
        if r.category == "code" and r.configured and r.blocking and r.result is not None:
            owners.setdefault(r.key.split("_", 1)[0], []).append(r.name)
    for r in runs:
        names = owners.get(r.key.split("_", 1)[0])
        if not names or r.configured or r.category != "code" or r.result is None:
            continue
        r.result = replace(
            r.result,
            summary=f"{r.result.summary}; not scored, the project uses {', '.join(names)}",
            metrics={**r.result.metrics, "advisory": True},
        )


def _non_blocking(result: ToolResult) -> ToolResult:
    """A linter MegaLinter runs as non-blocking (formatters, mostly): its issues weigh as info."""
    total = sum(int(n) for n in result.counts.values())
    if not total:
        return result
    return replace(result, counts={"info": total})


def read_reports(out: Path) -> list[LinterRun]:
    """One :class:`LinterRun` per linter MegaLinter ran, from the reports in ``out``."""
    report = out / "mega-linter-report.json"
    if not report.is_file():
        raise ReportError("MegaLinter wrote no mega-linter-report.json")
    try:
        data = json.loads(report.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ReportError(f"mega-linter-report.json is not JSON: {exc}") from exc
    runs: list[LinterRun] = []
    configs = _rules_configs(out)
    for linter in data.get("linters") or []:
        if not isinstance(linter, dict) or not linter.get("is_active", True):
            continue
        key = str(linter.get("name") or "")
        linter_name = str(linter.get("linter_name") or key)
        status = str(linter.get("status") or "")
        if not key or status in {"", "skipped"}:
            continue
        category = category_of(key, linter_name)
        if category is None:
            continue
        run = LinterRun(
            key=key,
            name=display_name(key, linter_name),
            category=category,
            configured=configs.get(key, "").startswith("[/"),
        )
        errors = int(linter.get("total_number_errors") or 0)
        warnings = int(linter.get("total_number_warnings") or 0)
        sarif = out / "sarif" / f"{key}.sarif"
        findings = _sarif_findings(sarif) if sarif.is_file() else None
        tail = _log_tail(out, key)
        if findings is not None and (findings or not errors):
            run.result = from_findings(findings, what="issue", clean="No issues")
        elif status == "error" and (category != "code" or _TOOL_FAILURE.search(tail)):
            # A scanner that failed (no network for its database, a crash) measured nothing.
            run.error = f"did not finish: {tail}" if tail else "did not finish (see its log)"
        else:
            # Linters without SARIF output report counts only.
            counts = {"low": errors, "info": warnings}
            fake = [Finding("low", f"{linter_name} error")] * errors + [
                Finding("info", f"{linter_name} warning")
            ] * warnings
            result = from_findings(fake, what="issue", clean="No issues")
            run.result = ToolResult(
                score=result.score,
                summary=(
                    f"{errors} error(s), {warnings} warning(s); details in its log"
                    if errors or warnings
                    else "No issues"
                ),
                metrics={**result.metrics, "details": "log only"},
                findings=(),
                counts={**result.counts, **counts},
                fingerprints=result.fingerprints,
            )
        if status == "warning" and run.result is not None:
            run.blocking = False
            run.result = _non_blocking(run.result)
        runs.append(run)
    _set_advisory(runs)
    return runs


def run(
    repo: Path,
    out: Path,
    *,
    main_language: str | None,
    image: str | None = None,
    disabled: list[str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    on_line: Callable[[str], None] | None = None,
) -> EngineOutcome:
    """Run MegaLinter on ``repo`` and read its reports; never raises for a linter's failure."""
    chosen = image_for(main_language, image)
    outcome = EngineOutcome(image=chosen)
    if docker() is None:
        outcome.error = "Docker (or Podman) is not installed"
        return outcome
    out.mkdir(parents=True, exist_ok=True)
    argv = command(repo.resolve(), out.resolve(), image=chosen, disabled=disabled)
    started = time.monotonic()
    log = out / "megalinter.console.log"
    try:
        with log.open("w", encoding="utf-8") as fh:
            fh.write(
                "$ "
                + " ".join(a if "PROXY" not in a.upper() else a.split("=")[0] + "=…" for a in argv)
                + "\n\n"
            )
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
            )
            assert proc.stdout is not None
            deadline = started + timeout_s
            for line in proc.stdout:
                fh.write(line)
                if on_line and ("Linted [" in line or "Pulling" in line):
                    on_line(re.sub(r"\x1b\[[0-9;]*m", "", line).strip())
                if time.monotonic() > deadline:
                    proc.kill()
                    break
            proc.wait(timeout=60)
    except OSError as exc:
        outcome.error = f"could not start {argv[0]}: {exc}"
        return outcome
    finally:
        outcome.seconds = round(time.monotonic() - started, 1)
    if time.monotonic() > started + timeout_s:
        outcome.error = f"stopped after {timeout_s} s"
        return outcome
    try:
        outcome.runs = read_reports(out)
    except ReportError as exc:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        outcome.error = f"{exc}" + (f": {' '.join(tail)[:300]}" if tail else "")
    return outcome
