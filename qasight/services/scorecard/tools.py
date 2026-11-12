"""The tools a release scorecard knows: what each checks, how ``qasight scorecard scan`` runs it, and its parser.

A tool is either a **repo** tool (reads the files: Bandit, Semgrep, gitleaks,
Trivy, ...) or an **app** tool (talks to a running copy: ZAP, Lighthouse, axe,
k6, Schemathesis), which the scan runs only when it is given ``--url``. Some
tools are **import only**: their run is too specific to the project (mutation
testing, coverage, a Locust file), so their report comes in through
``qasight scorecard import``.

QASight never installs a tool. A scan runs the ones on ``PATH`` and lists the
rest with the command that installs them.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from qasight.services.scorecard.parsers import PARSERS, ToolResult

Kind = Literal["repo", "app", "import"]

CATEGORIES: dict[str, str] = {
    "security": "Security",
    "supply-chain": "Supply chain",
    "code": "Code quality",
    "api": "API",
    "web": "Web",
    "accessibility": "Accessibility",
    "performance": "Performance",
    "tests": "Tests",
}


@dataclass(frozen=True)
class ScanContext:
    """What one tool's command needs: where the code is and where its report goes."""

    repo: Path
    out: Path
    url: str | None = None
    openapi: str | None = None
    # "owner/name" when the code is on GitHub (OpenSSF Scorecard reads it there).
    github_repo: str | None = None
    # Semgrep rules: a registry name (p/default) or a local folder or file of rules.
    semgrep_config: str | None = None


@dataclass(frozen=True)
class Command:
    argv: list[str]
    cwd: Path
    # The tool prints its report instead of writing a file: save stdout as the report.
    stdout_is_report: bool = False
    # Files the command needs written first (a k6 script), path -> text.
    files: dict[Path, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    category: str
    tagline: str
    # How the score is made, one sentence.
    how: str
    homepage: str
    kind: Kind
    # The report it reads, for `qasight scorecard import`.
    report: str
    parser: str
    # Languages it applies to; empty means any repository.
    languages: frozenset[str] = frozenset()
    default_on: bool = True
    binary: str | None = None
    install: str = ""
    # File name of the report a scan writes.
    report_name: str = "report.json"
    command: Callable[[ScanContext], Command | None] | None = None
    # Added to the error when the tool leaves no report (what usually went wrong).
    failure_hint: str = ""

    def installed(self) -> bool:
        return self.binary is not None and shutil.which(self.binary) is not None

    def parse(self, path: Path) -> ToolResult:
        return PARSERS[self.parser](path)


_FINDINGS_HOW = (
    "Starts at 100 and loses points per kind of finding by severity (critical 25, high 10, medium 4, low 1), "
    "repeats on a log scale; 100 points halve it."
)


# Folders never searched for manifests: vendored code and build output.
SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox", "vendor"}
)


def manifest_dirs(repo: Path, names: tuple[str, ...], depth: int = 2) -> list[Path]:
    """Folders up to ``depth`` levels below ``repo`` (the root first) holding any of ``names``."""
    found: list[Path] = []
    level = [repo]
    for _ in range(depth + 1):
        found += [d for d in level if any((d / n).is_file() for n in names)]
        nxt: list[Path] = []
        for d in level:
            try:
                children = sorted(p for p in d.iterdir() if p.is_dir())
            except OSError:
                continue
            nxt += [c for c in children if c.name not in SKIP_DIRS and not c.name.startswith(".")]
        level = nxt
    return found


def has_ruff_config(repo: Path) -> bool:
    """A ruff.toml, .ruff.toml or a pyproject.toml with [tool.ruff] in the repository's top two levels."""
    for folder in manifest_dirs(repo, ("ruff.toml", ".ruff.toml", "pyproject.toml")):
        if (folder / "ruff.toml").is_file() or (folder / ".ruff.toml").is_file():
            return True
        try:
            text = (folder / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "[tool.ruff" in text:
            return True
    return False


def _bandit(ctx: ScanContext) -> Command:
    skip = ",".join(
        f"./{d}"
        for d in (".git", ".venv", "venv", "node_modules", "tests", "test", "build", "dist")
    )
    return Command(
        ["bandit", "-r", ".", "-f", "json", "-o", str(ctx.out), "-q", "-x", skip, "--exit-zero"],
        ctx.repo,
    )


def _ruff(ctx: ScanContext) -> Command:
    argv = ["ruff", "check", ".", "--output-format", "json", "--exit-zero", "--no-cache"]
    if not has_ruff_config(ctx.repo):
        # No config of its own: Ruff's default rules only, never a config from a folder above
        # the repository or the user's own (it would judge the repo by someone else's style).
        argv.append("--isolated")
    return Command(argv, ctx.repo, stdout_is_report=True)


def _pip_audit(ctx: ScanContext) -> Command | None:
    # The root, else the first folder up to two levels down with a requirements file or project.
    folders = manifest_dirs(ctx.repo, ("requirements.txt", "pyproject.toml", "setup.py"))
    if not folders:
        return None
    folder = folders[0]
    target = ["-r", "requirements.txt"] if (folder / "requirements.txt").is_file() else ["."]
    return Command(
        ["pip-audit", *target, "-f", "json", "-o", str(ctx.out), "--progress-spinner", "off"],
        folder,
    )


# Lock file -> the audit that reads it. pnpm's output is npm 6's JSON; yarn 1 prints NDJSON.
_JS_AUDITS: tuple[tuple[str, list[str]], ...] = (
    ("package-lock.json", ["npm", "audit", "--json", "--package-lock-only"]),
    ("npm-shrinkwrap.json", ["npm", "audit", "--json", "--package-lock-only"]),
    ("pnpm-lock.yaml", ["pnpm", "audit", "--json"]),
    ("yarn.lock", ["yarn", "audit", "--json"]),
)


def _npm_audit(ctx: ScanContext) -> Command | None:
    # The root, else the first folder up to two levels down with a lock file (frontend/, client/).
    folders = manifest_dirs(ctx.repo, tuple(lock for lock, _ in _JS_AUDITS))
    if not folders:
        return None
    folder = folders[0]
    argv = next(argv for lock, argv in _JS_AUDITS if (folder / lock).is_file())
    return Command(list(argv), folder, stdout_is_report=True)


def _bundler_audit(ctx: ScanContext) -> Command | None:
    folders = manifest_dirs(ctx.repo, ("Gemfile.lock",))
    if not folders:
        return None
    return Command(
        ["bundle-audit", "check", "--format", "json", "--output", str(ctx.out)], folders[0]
    )


def _osv_scanner(ctx: ScanContext) -> Command:
    return Command(
        ["osv-scanner", "--format", "json", "--recursive", "."], ctx.repo, stdout_is_report=True
    )


def _gitleaks(ctx: ScanContext) -> Command:
    return Command(
        [
            "gitleaks",
            "detect",
            "--no-banner",
            "--no-git",
            "--source",
            ".",
            "--report-format",
            "json",
            "--report-path",
            str(ctx.out),
            "--exit-code",
            "0",
        ],
        ctx.repo,
    )


def _semgrep(ctx: ScanContext) -> Command:
    config = ctx.semgrep_config or "p/default"
    return Command(
        [
            "semgrep",
            "scan",
            "--config",
            config,
            "--sarif",
            "--output",
            str(ctx.out),
            "--metrics",
            "off",
            "--timeout",
            "30",
            ".",
        ],
        ctx.repo,
    )


def _trivy(ctx: ScanContext) -> Command:
    return Command(
        [
            "trivy",
            "fs",
            "--format",
            "json",
            "--output",
            str(ctx.out),
            "--scanners",
            "vuln,secret,misconfig",
            "--quiet",
            ".",
        ],
        ctx.repo,
    )


def _flawfinder(ctx: ScanContext) -> Command:
    return Command(
        ["flawfinder", "--sarif", "--minlevel", "3", "--quiet", "."],
        ctx.repo,
        stdout_is_report=True,
    )


def _zizmor(ctx: ScanContext) -> Command | None:
    if not (ctx.repo / ".github" / "workflows").is_dir():
        return None
    return Command(
        ["zizmor", "--format", "sarif", "--no-online-audits", "."], ctx.repo, stdout_is_report=True
    )


def _scorecard(ctx: ScanContext) -> Command | None:
    if not ctx.github_repo or not (
        os.environ.get("GITHUB_AUTH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    ):
        return None
    return Command(
        ["scorecard", f"--repo=github.com/{ctx.github_repo}", "--format", "json"],
        ctx.repo,
        stdout_is_report=True,
    )


def _zap(ctx: ScanContext) -> Command | None:
    if not ctx.url:
        return None
    work = ctx.out.parent
    return Command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "-v",
            f"{work}:/zap/wrk:rw",
            "ghcr.io/zaproxy/zaproxy:stable",
            "zap-baseline.py",
            "-t",
            ctx.url,
            "-J",
            ctx.out.name,
            "-I",
        ],
        ctx.repo,
    )


def _lighthouse(ctx: ScanContext) -> Command | None:
    if not ctx.url:
        return None
    return Command(
        [
            "lighthouse",
            ctx.url,
            "--output",
            "json",
            "--output-path",
            str(ctx.out),
            "--quiet",
            "--chrome-flags=--headless=new --no-sandbox",
        ],
        ctx.repo,
    )


def _axe(ctx: ScanContext) -> Command | None:
    if not ctx.url:
        return None
    return Command(["axe", ctx.url, "--save", ctx.out.name, "--dir", str(ctx.out.parent)], ctx.repo)


_K6_SCRIPT = """import http from "k6/http";
import { check } from "k6";
export const options = { vus: 10, duration: "20s" };
export default function () {
  const res = http.get(__ENV.TARGET_URL);
  check(res, { "status < 400": (r) => r.status < 400 });
}
"""


def _k6(ctx: ScanContext) -> Command | None:
    if not ctx.url:
        return None
    script = ctx.out.parent / "k6-smoke.js"
    return Command(
        [
            "k6",
            "run",
            "--quiet",
            "--summary-export",
            str(ctx.out),
            "-e",
            f"TARGET_URL={ctx.url}",
            str(script),
        ],
        ctx.repo,
        files={script: _K6_SCRIPT},
    )


def _schemathesis(ctx: ScanContext) -> Command | None:
    if not ctx.openapi:
        return None
    argv = [
        "schemathesis",
        "run",
        ctx.openapi,
        "--report",
        "junit",
        "--report-junit-path",
        str(ctx.out),
        "--max-examples",
        "20",
    ]
    if ctx.url:
        argv += ["--url", ctx.url]
    return Command(argv, ctx.repo)


TOOLS: tuple[Tool, ...] = (
    # --- Security -------------------------------------------------------------
    Tool(
        id="bandit",
        name="Bandit",
        category="security",
        tagline="Common security mistakes in Python code.",
        how=_FINDINGS_HOW,
        homepage="https://bandit.readthedocs.io",
        kind="repo",
        report="Bandit JSON (bandit -f json)",
        parser="bandit",
        languages=frozenset({"python"}),
        binary="bandit",
        install="pip install bandit",
        command=_bandit,
    ),
    Tool(
        id="semgrep",
        name="Semgrep",
        category="security",
        tagline="Security rules for 30+ languages (the p/default rule set, or your own rules).",
        how=_FINDINGS_HOW,
        homepage="https://semgrep.dev",
        kind="repo",
        report="SARIF (semgrep --sarif)",
        parser="sarif",
        binary="semgrep",
        install="pip install semgrep",
        report_name="report.sarif",
        command=_semgrep,
        failure_hint=(
            "The p/default rules download from semgrep.dev. Offline, clone "
            "github.com/semgrep/semgrep-rules and pass --semgrep-config with a folder of it "
            "(python, java, ruby, javascript, ...)."
        ),
    ),
    Tool(
        id="gitleaks",
        name="gitleaks",
        category="security",
        tagline="Passwords, keys and tokens committed in the files (test and fixture files left out).",
        how=_FINDINGS_HOW
        + " Private and cloud keys are critical, provider tokens high, generic matches medium; each secret counts once.",
        homepage="https://gitleaks.io",
        kind="repo",
        report="gitleaks JSON (--report-format json)",
        parser="gitleaks",
        binary="gitleaks",
        install="brew install gitleaks, or a release from github.com/gitleaks/gitleaks",
        command=_gitleaks,
    ),
    Tool(
        id="flawfinder",
        name="Flawfinder",
        category="security",
        tagline="Risky C and C++ calls (buffer overflows, format strings).",
        how=_FINDINGS_HOW,
        homepage="https://dwheeler.com/flawfinder/",
        kind="repo",
        report="SARIF (flawfinder --sarif)",
        parser="sarif",
        languages=frozenset({"c"}),
        binary="flawfinder",
        install="pip install flawfinder",
        report_name="report.sarif",
        command=_flawfinder,
    ),
    Tool(
        id="zap",
        name="OWASP ZAP",
        category="security",
        tagline="Passive scan of the running app for web security issues.",
        how=_FINDINGS_HOW,
        homepage="https://www.zaproxy.org",
        kind="app",
        report="ZAP JSON report (-J report.json)",
        parser="zap",
        binary="docker",
        install="Docker (the scan runs ghcr.io/zaproxy/zaproxy:stable)",
        command=_zap,
    ),
    # --- Supply chain ---------------------------------------------------------
    Tool(
        id="pip-audit",
        name="pip-audit",
        category="supply-chain",
        tagline="Python dependencies with known vulnerabilities.",
        how=_FINDINGS_HOW + " Each vulnerable package is high.",
        homepage="https://github.com/pypa/pip-audit",
        kind="repo",
        report="pip-audit JSON (pip-audit -f json)",
        parser="pip-audit",
        languages=frozenset({"python"}),
        binary="pip-audit",
        install="pip install pip-audit",
        command=_pip_audit,
    ),
    Tool(
        id="npm-audit",
        name="npm audit",
        category="supply-chain",
        tagline="JavaScript dependencies with known vulnerabilities (npm, pnpm or yarn lock file).",
        how=_FINDINGS_HOW,
        homepage="https://docs.npmjs.com/cli/commands/npm-audit",
        kind="repo",
        report="npm audit --json, pnpm audit --json or yarn audit --json",
        parser="npm-audit",
        languages=frozenset({"node"}),
        binary="npm",
        install="Node.js (npm comes with it; pnpm or yarn for their lock files)",
        command=_npm_audit,
    ),
    Tool(
        id="bundler-audit",
        name="bundler-audit",
        category="supply-chain",
        tagline="Ruby gems in Gemfile.lock with known vulnerabilities.",
        how=_FINDINGS_HOW,
        homepage="https://github.com/rubysec/bundler-audit",
        kind="repo",
        report="bundler-audit JSON (bundle-audit check --format json)",
        parser="bundler-audit",
        languages=frozenset({"ruby"}),
        binary="bundle-audit",
        install="gem install bundler-audit",
        command=_bundler_audit,
        failure_hint="It needs the ruby-advisory-db: run `bundle-audit update` once with network.",
    ),
    Tool(
        id="osv-scanner",
        name="OSV-Scanner",
        category="supply-chain",
        tagline="Known vulnerabilities in any lock file: Maven, Gradle, Go, Ruby, Rust, PHP, ...",
        how=_FINDINGS_HOW,
        homepage="https://google.github.io/osv-scanner/",
        kind="repo",
        report="osv-scanner JSON (--format json)",
        parser="osv-scanner",
        languages=frozenset({"java", "go", "ruby"}),
        binary="osv-scanner",
        install="brew install osv-scanner, or go install github.com/google/osv-scanner/v2/cmd/osv-scanner@latest",
        command=_osv_scanner,
        failure_hint="It reads the vulnerability database from osv.dev, so it needs network.",
    ),
    Tool(
        id="trivy",
        name="Trivy",
        category="supply-chain",
        tagline="Vulnerable packages, secrets and misconfigurations in the files.",
        how=_FINDINGS_HOW,
        homepage="https://trivy.dev",
        kind="repo",
        report="Trivy JSON or SARIF (trivy fs --format json)",
        parser="trivy",
        binary="trivy",
        install="brew install trivy, or see trivy.dev",
        command=_trivy,
    ),
    Tool(
        id="openssf-scorecard",
        name="OpenSSF Scorecard",
        category="supply-chain",
        tagline="How the GitHub repository is run: branch protection, pinned actions, reviews.",
        how="Its own 0-10 score, times ten.",
        homepage="https://scorecard.dev",
        kind="repo",
        report="Scorecard JSON (--format json)",
        parser="openssf-scorecard",
        binary="scorecard",
        install="brew install scorecard; needs GITHUB_AUTH_TOKEN",
        command=_scorecard,
    ),
    Tool(
        id="zizmor",
        name="zizmor",
        category="supply-chain",
        tagline="Security problems in GitHub Actions workflows.",
        how=_FINDINGS_HOW,
        homepage="https://docs.zizmor.sh",
        kind="repo",
        report="SARIF (zizmor --format sarif)",
        parser="sarif",
        languages=frozenset({"github-actions"}),
        binary="zizmor",
        install="pip install zizmor",
        report_name="report.sarif",
        command=_zizmor,
    ),
    # --- Code quality ---------------------------------------------------------
    Tool(
        id="ruff",
        name="Ruff",
        category="code",
        tagline="Python lint: bugs, unused code and style, with the repo's own config.",
        how=_FINDINGS_HOW + " Most lint issues are low.",
        homepage="https://docs.astral.sh/ruff/",
        kind="repo",
        report="Ruff JSON (ruff check --output-format json)",
        parser="ruff",
        languages=frozenset({"python"}),
        binary="ruff",
        install="pip install ruff",
        command=_ruff,
    ),
    Tool(
        id="sarif",
        name="Other SARIF tool",
        category="code",
        tagline="Any analyzer that writes SARIF (CodeQL, ESLint, Checkov, ...).",
        how=_FINDINGS_HOW,
        homepage="https://sarifweb.azurewebsites.net",
        kind="import",
        report="Any SARIF 2.1 file",
        parser="sarif",
        default_on=False,
    ),
    # --- Running app ----------------------------------------------------------
    Tool(
        id="schemathesis",
        name="Schemathesis",
        category="api",
        tagline="Generated requests from the OpenAPI schema; finds 500s and schema mismatches.",
        how="Share of its checks that passed.",
        homepage="https://schemathesis.io",
        kind="app",
        report="JUnit XML (--report junit)",
        parser="junit",
        binary="schemathesis",
        install="pip install schemathesis",
        report_name="report.xml",
        command=_schemathesis,
    ),
    Tool(
        id="lighthouse",
        name="Lighthouse",
        category="web",
        tagline="Performance, best practices and SEO of the web UI.",
        how="Mean of its category scores.",
        homepage="https://developer.chrome.com/docs/lighthouse",
        kind="app",
        report="Lighthouse JSON (--output json) or an lhci manifest",
        parser="lighthouse",
        binary="lighthouse",
        install="npm install -g lighthouse",
        command=_lighthouse,
    ),
    Tool(
        id="axe",
        name="axe",
        category="accessibility",
        tagline="WCAG accessibility violations on the page.",
        how=_FINDINGS_HOW + " Impact maps to severity (critical, serious, moderate, minor).",
        homepage="https://www.deque.com/axe/",
        kind="app",
        report="axe-core results JSON",
        parser="axe",
        binary="axe",
        install="npm install -g @axe-core/cli",
        command=_axe,
    ),
    Tool(
        id="k6",
        name="k6",
        category="performance",
        tagline="A short load test: 10 users for 20 seconds.",
        how="Share of thresholds met; without thresholds, successful requests scaled down when p95 is over 500 ms.",
        homepage="https://k6.io",
        kind="app",
        report="k6 summary JSON (--summary-export)",
        parser="k6",
        binary="k6",
        install="brew install k6, or see k6.io",
        command=_k6,
    ),
    Tool(
        id="locust",
        name="Locust",
        category="performance",
        tagline="Your own Locust load test.",
        how="Successful requests, scaled down when p95 is over 500 ms.",
        homepage="https://locust.io",
        kind="import",
        report="Locust stats CSV (--csv prefix, the *_stats.csv file)",
        parser="locust",
        default_on=False,
    ),
    # --- Tests ----------------------------------------------------------------
    Tool(
        id="coverage",
        name="Coverage",
        category="tests",
        tagline="Lines the tests run (coverage.py, istanbul, Cobertura).",
        how="Line coverage percent.",
        homepage="https://coverage.readthedocs.io",
        kind="import",
        report="coverage.py JSON, istanbul json-summary or Cobertura XML",
        parser="coverage",
    ),
    Tool(
        id="mutation",
        name="Mutation testing",
        category="tests",
        tagline="Do the tests notice small changes to the code? (mutmut, Stryker)",
        how="Share of mutants the tests caught.",
        homepage="https://mutmut.readthedocs.io",
        kind="import",
        report="mutmut cicd stats JSON (mutmut export-cicd-stats) or a Stryker JSON report",
        parser="mutation",
    ),
    Tool(
        id="junit",
        name="Test results",
        category="tests",
        tagline="Any JUnit XML report (a release's E2E suite, a smoke test).",
        how="Share of tests that passed.",
        homepage="https://github.com/testmoapp/junitxml",
        kind="import",
        report="JUnit XML",
        parser="junit",
        default_on=False,
    ),
)

TOOLS_BY_ID: dict[str, Tool] = {tool.id: tool for tool in TOOLS}


def get_tool(tool_id: str) -> Tool:
    try:
        return TOOLS_BY_ID[tool_id]
    except KeyError:
        known = ", ".join(TOOLS_BY_ID)
        raise KeyError(f"unknown tool {tool_id!r}; known tools: {known}") from None
