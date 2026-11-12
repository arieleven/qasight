"""Read each tool's own report file into one :class:`ToolResult` (a 0-100 score and findings).

Every parser is pure: a path in, a result out, no network and no tool installed.
Two kinds of score:

* **Findings tools** (Bandit, Semgrep, gitleaks, Trivy, ZAP, axe, ...) lose points
  per kind of finding (a rule, an advisory) by severity (:data:`SEVERITY_POINTS`).
  Repeats of one rule cost more on a log scale (``1 + log2(n)`` times), so 200
  ``assert`` warnings don't outweigh one injection. The points become a score
  on a curve, ``100 * K / (K + points)`` with ``K = 100``: no findings is 100,
  one high finding 91, one critical 80, 100 points 50. A big repository with
  many findings gets a low score but never a flat 0, so versions stay comparable.
* **Ratio tools** (Lighthouse, coverage, mutation testing, Schemathesis, OpenSSF
  Scorecard, load tests) already measure a share: their score is that share.

A report the parser can't read raises :class:`ReportError` with a sentence that
says what was expected.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET  # noqa: S405 - parsed with no DTD or entities (_xml_root)
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Points one finding costs, by severity.
SEVERITY_POINTS: dict[str, float] = {
    "critical": 25.0,
    "high": 10.0,
    "medium": 4.0,
    "low": 1.0,
    "info": 0.0,
}
SEVERITIES = tuple(SEVERITY_POINTS)
# The curve's half point: this many points halve the score.
CURVE_K = 100.0
# Findings kept per tool result; the counts cover all of them.
MAX_FINDINGS = 50
# Fingerprints kept per tool result, to find new findings next version.
MAX_FINGERPRINTS = 5000


class ReportError(ValueError):
    """The file isn't the report the tool writes (or is empty)."""


@dataclass(frozen=True)
class Finding:
    severity: str
    title: str
    detail: str = ""
    location: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "location": self.location,
        }


@dataclass(frozen=True)
class ToolResult:
    score: int
    summary: str
    # Measured values worth showing: {"high": 2, "p95_ms": 340.0, ...}.
    metrics: dict[str, float | int | str] = field(default_factory=dict)
    findings: tuple[Finding, ...] = ()
    # Findings per severity, over all of them (not only the kept ones).
    counts: dict[str, int] = field(default_factory=dict)
    # Findings tools only: every finding and how to score a subset of them, so an
    # ignore file (:mod:`qasight.services.scorecard.ignore`) can drop some and rescore.
    all_findings: tuple[Finding, ...] = field(default=(), repr=False, compare=False)
    rescore: Callable[[list[Finding]], ToolResult] | None = field(
        default=None, repr=False, compare=False
    )
    # "severity:hash" of every counted finding, to tell new findings from known ones.
    fingerprints: tuple[str, ...] = ()
    # Set for a linter an engine ran, which isn't in the tool list: its name and category.
    name: str | None = None
    category: str | None = None


def _clamp(value: float) -> int:
    if math.isnan(value):
        return 0
    return max(0, min(100, round(value)))


def curve(points: float) -> int:
    """Findings points to a 0-100 score."""
    return _clamp(100.0 * CURVE_K / (CURVE_K + max(0.0, points)))


def severity_of(raw: Any, default: str = "medium") -> str:
    """A tool's severity word (or SARIF level, ZAP risk code) as one of :data:`SEVERITIES`."""
    text = str(raw or "").strip().lower()
    mapping = {
        "critical": "critical",
        "blocker": "critical",
        "high": "high",
        "error": "high",
        "serious": "high",
        "major": "high",
        "medium": "medium",
        "moderate": "medium",
        "warning": "medium",
        "low": "low",
        "minor": "low",
        "note": "low",
        "info": "info",
        "informational": "info",
        "none": "info",
        "unknown": "low",
    }
    return mapping.get(text, default)


def finding_points(findings: Iterable[Finding]) -> float:
    """Severity points per kind of finding, repeats on a log scale."""
    rules: dict[tuple[str, str], int] = {}
    for finding in findings:
        key = (finding.severity, finding.title)
        rules[key] = rules.get(key, 0) + 1
    return sum(SEVERITY_POINTS[sev] * (1.0 + math.log2(n)) for (sev, _title), n in rules.items())


def plural(n: int, what: str) -> str:
    """``3 vulnerable dependencies``, ``1 issue``."""
    if n == 1:
        return f"{n} {what}"
    if what.endswith("y") and what[-2:-1] not in "aeiou":
        return f"{n} {what[:-1]}ies"
    return f"{n} {what}s"


def from_findings(
    findings: Iterable[Finding],
    *,
    what: str,
    clean: str,
    extra: dict[str, float | int | str] | None = None,
) -> ToolResult:
    """Score a list of findings on the curve; ``what`` names one finding ("issue")."""
    base_extra = extra
    every = sorted(findings, key=lambda f: SEVERITIES.index(f.severity))
    # Findings in tests, vendored or generated code and docs are shown apart and not counted.
    items = [f for f in every if _counts_as_source(f.location)]
    elsewhere = len(every) - len(items)
    if elsewhere:
        extra = {**(extra or {}), "not_counted": elsewhere}
    counts = {s: 0 for s in SEVERITIES}
    for finding in items:
        counts[finding.severity] += 1
    points = finding_points(items)
    shown = {s: n for s, n in counts.items() if n}
    if not items:
        summary = clean
    else:
        parts = ", ".join(f"{n} {s}" for s, n in shown.items())
        summary = f"{plural(len(items), what)} ({parts})"
    metrics: dict[str, float | int | str] = {"findings": len(items), **shown, **(extra or {})}
    return ToolResult(
        score=curve(points),
        summary=summary,
        metrics=metrics,
        findings=tuple(items[:MAX_FINDINGS]),
        counts=counts,
        all_findings=tuple(items),
        rescore=lambda kept: from_findings(kept, what=what, clean=clean, extra=base_extra),
        fingerprints=tuple(fingerprint(f) for f in items[:MAX_FINGERPRINTS]),
    )


def _counts_as_source(location: str) -> bool:
    """Whether a finding at ``location`` counts: anything that isn't a file path, or a file in source code."""
    if not location or ("://" in location and not location.startswith("file://")):
        return True
    from qasight.services.scorecard.paths import classify

    return classify(location) == "source"


def fingerprint(finding: Finding) -> str:
    """``severity:hash`` of what a finding is and which file it is in (not the line, which moves)."""
    path = re.sub(r":\d+(:\d+)?$", "", finding.location)
    digest = hashlib.sha1(f"{finding.title}\n{path}".encode(), usedforsecurity=False).hexdigest()
    return f"{finding.severity}:{digest[:12]}"


def _load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        raise ReportError(f"can't read {path}: {exc}") from exc
    if not text:
        raise ReportError(f"{path.name} is empty")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some tools print a banner line before the JSON.
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start > 0:
            try:
                return json.loads(text[start:])
            except json.JSONDecodeError:
                pass
        raise ReportError(f"{path.name} is not JSON") from None


def _expect(cond: bool, message: str) -> None:
    if not cond:
        raise ReportError(message)


def _short(text: Any, limit: int = 300) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


# --- SARIF (Semgrep, zizmor, flawfinder, CodeQL, Trivy --format sarif, ...) ------


def parse_sarif(path: Path) -> ToolResult:
    data = _load_json(path)
    _expect(
        isinstance(data, dict) and isinstance(data.get("runs"), list),
        "expected a SARIF file (a JSON object with runs)",
    )
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
            severity = _sarif_severity(level, props)
            location = ""
            locs = result.get("locations") or []
            if locs:
                phys = (locs[0] or {}).get("physicalLocation") or {}
                uri = (phys.get("artifactLocation") or {}).get("uri") or ""
                line = (phys.get("region") or {}).get("startLine")
                location = f"{uri}:{line}" if uri and line else uri
            message = (result.get("message") or {}).get("text") or ""
            title = rule_id or _short(message, 80) or "finding"
            findings.append(Finding(severity, title, _short(message), location))
    return from_findings(findings, what="finding", clean="No findings")


def _sarif_severity(level: Any, props: dict[str, Any]) -> str:
    score = props.get("security-severity")
    try:
        value = float(score) if score is not None else None
    except (TypeError, ValueError):
        value = None
    if value is not None:
        if value >= 9.0:
            return "critical"
        if value >= 7.0:
            return "high"
        if value >= 4.0:
            return "medium"
        return "low"
    return severity_of(level, "medium")


# --- Python ----------------------------------------------------------------------


def parse_bandit(path: Path) -> ToolResult:
    data = _load_json(path)
    _expect(
        isinstance(data, dict) and "results" in data,
        "expected Bandit's JSON report (bandit -f json)",
    )
    findings = [
        Finding(
            severity_of(r.get("issue_severity"), "low"),
            f"{r.get('test_id', '')} {r.get('test_name', '')}".strip(),
            _short(r.get("issue_text")),
            f"{r.get('filename', '')}:{r.get('line_number', '')}",
        )
        for r in data.get("results") or []
        if isinstance(r, dict)
    ]
    loc = ((data.get("metrics") or {}).get("_totals") or {}).get("loc")
    extra: dict[str, float | int | str] = (
        {"lines_scanned": int(loc)} if isinstance(loc, (int, float)) else {}
    )
    return from_findings(findings, what="issue", clean="No issues", extra=extra)


def parse_ruff(path: Path) -> ToolResult:
    data = _load_json(path)
    _expect(isinstance(data, list), "expected Ruff's JSON output (ruff check --output-format json)")
    findings = [
        Finding(
            "medium" if str(d.get("code") or "").startswith(("S", "B", "F")) else "low",
            str(d.get("code") or "ruff"),
            _short(d.get("message")),
            f"{d.get('filename', '')}:{(d.get('location') or {}).get('row', '')}",
        )
        for d in data
        if isinstance(d, dict)
    ]
    return from_findings(findings, what="lint issue", clean="No lint issues")


def parse_pip_audit(path: Path) -> ToolResult:
    data = _load_json(path)
    deps = data.get("dependencies") if isinstance(data, dict) else data
    if not isinstance(deps, list):
        raise ReportError("expected pip-audit's JSON report (pip-audit -f json)")
    findings: list[Finding] = []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        for vuln in dep.get("vulns") or []:
            fixes = ", ".join(vuln.get("fix_versions") or [])
            findings.append(
                Finding(
                    "high",
                    f"{dep.get('name')} {dep.get('version', '')}".strip(),
                    _short(f"{vuln.get('id', '')}: fixed in {fixes or 'no release yet'}"),
                    str(vuln.get("id") or ""),
                )
            )
    return from_findings(
        findings,
        what="vulnerable dependency",
        clean="No known vulnerabilities",
        extra={"packages": len(deps)},
    )


# --- JavaScript ------------------------------------------------------------------


def parse_npm_audit(path: Path) -> ToolResult:
    data = _load_json(path)
    _expect(
        isinstance(data, dict) and ("vulnerabilities" in data or "advisories" in data),
        "expected npm audit --json output",
    )
    findings: list[Finding] = []
    for name, vuln in (data.get("vulnerabilities") or {}).items():
        if not isinstance(vuln, dict):
            continue
        via = [
            str(v.get("title"))
            for v in vuln.get("via") or []
            if isinstance(v, dict) and v.get("title")
        ]
        findings.append(
            Finding(
                severity_of(vuln.get("severity"), "medium"),
                str(name),
                _short("; ".join(via) or vuln.get("range") or ""),
                str(name),
            )
        )
    for adv in (data.get("advisories") or {}).values():  # npm 6
        if isinstance(adv, dict):
            findings.append(
                Finding(
                    severity_of(adv.get("severity")),
                    str(adv.get("module_name")),
                    _short(adv.get("title")),
                    "",
                )
            )
    return from_findings(findings, what="vulnerable package", clean="No known vulnerabilities")


# --- Any language ----------------------------------------------------------------


# Paths of tests, fixtures and mocks: the "secrets" there are sample data. A folder
# named like one, or a file such as test_keys.py, keys.spec.ts or expressionTestData.json.
_TEST_DIR = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|fixtures?|__fixtures__|mocks?|__mocks__|testdata|test-data|test_data|testing)(/|$)",
    re.IGNORECASE,
)
_TEST_FILE = re.compile(
    r"(^|[._-])(tests?|specs?|fixtures?|mocks?)([._-]|$)|[a-z0-9](Test|Spec|Fixture|Mock)([A-Z0-9._-]|$)"
)


def is_test_path(path: str) -> bool:
    """True for a file under a test, fixture or mock folder, or named like one."""
    path = path.replace("\\", "/").lstrip("./")
    folder, _, name = path.rpartition("/")
    return bool(_TEST_DIR.search(folder + "/") or _TEST_FILE.search(name))


# gitleaks rule ids -> severity. Keys that open a cloud account or sign anything are
# critical; a "generic" match (any long random string next to the word key) is medium.
_GITLEAKS_CRITICAL = ("private-key", "aws-", "gcp-", "azure-", "github-app", "github-pat")
_GITLEAKS_MEDIUM = ("generic-", "jwt", "hashicorp-tf-password", "sidekiq-secret")


def gitleaks_severity(rule: str) -> str:
    rule = rule.lower()
    if rule.startswith(_GITLEAKS_MEDIUM):
        return "medium"
    if rule.startswith(_GITLEAKS_CRITICAL) or rule.endswith("private-key"):
        return "critical"
    return "high"


def parse_gitleaks(path: Path) -> ToolResult:
    """gitleaks' JSON: each distinct secret once, test and fixture files left out."""
    data = _load_json(path)
    _expect(isinstance(data, list), "expected gitleaks' JSON report (--report-format json)")
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    in_tests = 0
    for d in data:
        if not isinstance(d, dict):
            continue
        file = str(d.get("File") or "")
        if is_test_path(file):
            in_tests += 1
            continue
        rule = str(d.get("RuleID") or d.get("Description") or "secret")
        secret = str(d.get("Secret") or "")
        key = (
            (rule, secret)
            if secret and secret != "REDACTED"
            else (rule, f"{file}:{d.get('StartLine', '')}")
        )
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            Finding(
                gitleaks_severity(rule),
                rule,
                _short(d.get("Description")),
                f"{file}:{d.get('StartLine', '')}",
            )
        )
    extra: dict[str, float | int | str] = {"in_test_files": in_tests} if in_tests else {}
    result = from_findings(findings, what="leaked secret", clean="No secrets found", extra=extra)
    if in_tests:
        result = _with_note(result, f"{in_tests} in test or fixture files not counted")
    return result


def _with_note(result: ToolResult, note: str) -> ToolResult:
    from dataclasses import replace

    return replace(result, summary=f"{result.summary}; {note}")


def parse_trivy(path: Path) -> ToolResult:
    data = _load_json(path)
    if isinstance(data, dict) and "runs" in data:
        return parse_sarif(path)
    _expect(
        isinstance(data, dict) and ("Results" in data or "SchemaVersion" in data),
        "expected Trivy's JSON report (trivy fs --format json)",
    )
    findings: list[Finding] = []
    for result in data.get("Results") or []:
        target = str(result.get("Target") or "")
        for v in result.get("Vulnerabilities") or []:
            findings.append(
                Finding(
                    severity_of(v.get("Severity"), "medium"),
                    f"{v.get('PkgName', '')} {v.get('InstalledVersion', '')}".strip(),
                    _short(f"{v.get('VulnerabilityID', '')}: {v.get('Title', '')}"),
                    target,
                )
            )
        for m in result.get("Misconfigurations") or []:
            if str(m.get("Status") or "FAIL").upper() == "PASS":
                continue
            findings.append(
                Finding(
                    severity_of(m.get("Severity"), "medium"),
                    str(m.get("ID") or "misconfig"),
                    _short(m.get("Title")),
                    target,
                )
            )
        for s in result.get("Secrets") or []:
            findings.append(
                Finding(
                    "critical",
                    str(s.get("RuleID") or "secret"),
                    _short(s.get("Title")),
                    f"{target}:{s.get('StartLine', '')}",
                )
            )
    return from_findings(
        findings, what="finding", clean="No vulnerabilities, secrets or misconfigurations"
    )


def parse_openssf_scorecard(path: Path) -> ToolResult:
    data = _load_json(path)
    _expect(
        isinstance(data, dict) and "checks" in data,
        "expected OpenSSF Scorecard's JSON output (--format json)",
    )
    overall = data.get("score")
    findings: list[Finding] = []
    metrics: dict[str, float | int | str] = {}
    for check in data.get("checks") or []:
        name = str(check.get("name") or "")
        score = check.get("score")
        if isinstance(score, (int, float)) and score >= 0:
            metrics[name] = score
            if score < 10:
                severity = "high" if score < 4 else "medium" if score < 8 else "low"
                findings.append(
                    Finding(severity, name, _short(check.get("reason")), f"score {score}/10")
                )
    _expect(isinstance(overall, (int, float)), "the Scorecard report has no overall score")
    items = sorted(findings, key=lambda f: SEVERITIES.index(f.severity))
    counts = {s: sum(1 for f in items if f.severity == s) for s in SEVERITIES}
    return ToolResult(
        score=_clamp(float(overall) * 10),
        summary=f"{float(overall):.1f} of 10 over {len(metrics)} checks",
        metrics={"score_of_10": float(overall), **metrics},
        findings=tuple(items[:MAX_FINDINGS]),
        counts=counts,
    )


# --- Live app: web, API, load ----------------------------------------------------


def parse_zap(path: Path) -> ToolResult:
    data = _load_json(path)
    _expect(
        isinstance(data, dict) and "site" in data, "expected ZAP's JSON report (-J report.json)"
    )
    risk = {"3": "high", "2": "medium", "1": "low", "0": "info"}
    findings: list[Finding] = []
    sites = data.get("site") or []
    for site in sites if isinstance(sites, list) else [sites]:
        for alert in (site or {}).get("alerts") or []:
            instances = alert.get("instances") or []
            uri = (instances[0] or {}).get("uri", "") if instances else ""
            findings.append(
                Finding(
                    risk.get(str(alert.get("riskcode")), "low"),
                    str(alert.get("alert") or alert.get("name") or "alert"),
                    _short(
                        f"{alert.get('count', len(instances))} instance(s). {alert.get('solution', '')}"
                    ),
                    uri,
                )
            )
    return from_findings(findings, what="alert", clean="No alerts")


def parse_axe(path: Path) -> ToolResult:
    data = _load_json(path)
    pages = data if isinstance(data, list) else [data]
    _expect(
        bool(pages) and all(isinstance(p, dict) and "violations" in p for p in pages),
        "expected axe-core results JSON (with violations)",
    )
    impact = {"critical": "critical", "serious": "high", "moderate": "medium", "minor": "low"}
    findings: list[Finding] = []
    for page in pages:
        for v in page.get("violations") or []:
            nodes = v.get("nodes") or []
            findings.append(
                Finding(
                    impact.get(str(v.get("impact")), "medium"),
                    str(v.get("id") or "violation"),
                    _short(
                        f"{v.get('help', '')} ({len(nodes)} element{'s' if len(nodes) != 1 else ''})"
                    ),
                    str(page.get("url") or ""),
                )
            )
    return from_findings(
        findings, what="violation", clean="No accessibility violations", extra={"pages": len(pages)}
    )


def parse_lighthouse(path: Path) -> ToolResult:
    data = _load_json(path)
    if isinstance(data, list):  # lhci manifest: [{summary: {performance: 0.9, ...}}]
        summaries = [d.get("summary") or {} for d in data if isinstance(d, dict)]
        _expect(bool(summaries), "expected a Lighthouse report or an lhci manifest")
        keys = sorted({k for s in summaries for k in s})
        cats = {k: sum(float(s.get(k) or 0) for s in summaries) / len(summaries) for k in keys}
    else:
        _expect(
            isinstance(data, dict) and "categories" in data,
            "expected a Lighthouse JSON report (--output json)",
        )
        cats = {
            str(key): float(cat["score"])
            for key, cat in (data.get("categories") or {}).items()
            if isinstance(cat, dict) and isinstance(cat.get("score"), (int, float))
        }
    _expect(bool(cats), "the Lighthouse report has no category scores")
    scores = {k: _clamp(v * 100) for k, v in cats.items()}
    findings = tuple(
        Finding("high" if s < 50 else "medium" if s < 90 else "low", k, f"{s} of 100", "")
        for k, s in sorted(scores.items(), key=lambda kv: kv[1])
        if s < 90
    )
    mean = sum(scores.values()) / len(scores)
    return ToolResult(
        score=_clamp(mean),
        summary=", ".join(f"{k} {s}" for k, s in scores.items()),
        metrics=dict(scores),
        findings=findings,
        counts={s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES},
    )


def _latency_factor(p95_ms: float | None) -> float:
    """1.0 up to 500 ms, falling to 0.5 at 3.5 s and slower."""
    if p95_ms is None:
        return 1.0
    return max(0.5, min(1.0, 1.0 - (p95_ms - 500.0) / 6000.0))


def _load_result(
    *, requests: float, failures: float, p95_ms: float | None, extra: dict[str, float | int | str]
) -> ToolResult:
    error_rate = failures / requests if requests > 0 else 0.0
    score = _clamp(100.0 * (1.0 - error_rate) * _latency_factor(p95_ms))
    findings: list[Finding] = []
    if error_rate > 0:
        findings.append(
            Finding(
                "high" if error_rate > 0.01 else "medium",
                "Failed requests",
                f"{error_rate:.2%} of {int(requests)} requests failed",
                "",
            )
        )
    if p95_ms is not None and p95_ms > 1000:
        findings.append(Finding("medium", "Slow responses", f"p95 {p95_ms:.0f} ms", ""))
    p95_text = f", p95 {p95_ms:.0f} ms" if p95_ms is not None else ""
    return ToolResult(
        score=score,
        summary=f"{int(requests)} requests, {error_rate:.2%} failed{p95_text}",
        metrics={
            "requests": int(requests),
            "error_rate_pct": round(100 * error_rate, 2),
            **({"p95_ms": round(p95_ms, 1)} if p95_ms is not None else {}),
            **extra,
        },
        findings=tuple(findings),
        counts={s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES},
    )


def parse_k6(path: Path) -> ToolResult:
    data = _load_json(path)
    _expect(
        isinstance(data, dict) and isinstance(data.get("metrics"), dict),
        "expected k6's summary JSON (k6 run --summary-export)",
    )
    metrics = data["metrics"]

    def val(name: str, *keys: str) -> float | None:
        m = metrics.get(name) or {}
        m = m.get("values", m)  # handleSummary format nests under "values"
        for key in keys:
            if isinstance(m.get(key), (int, float)):
                return float(m[key])
        return None

    reqs = val("http_reqs", "count") or 0.0
    failed_rate = val("http_req_failed", "rate", "value") or 0.0
    p95 = val("http_req_duration", "p(95)")
    thresholds: list[bool] = []
    for m in metrics.values():
        for ok in ((m or {}).get("thresholds") or {}).values():
            thresholds.append(bool(ok.get("ok", not ok)) if isinstance(ok, dict) else not bool(ok))
    result = _load_result(requests=reqs, failures=failed_rate * reqs, p95_ms=p95, extra={})
    if thresholds:
        passed = sum(thresholds)
        return ToolResult(
            score=_clamp(100.0 * passed / len(thresholds)),
            summary=f"{passed} of {len(thresholds)} thresholds met; {result.summary}",
            metrics={**result.metrics, "thresholds": len(thresholds), "thresholds_met": passed},
            findings=result.findings,
            counts=result.counts,
        )
    return result


def parse_locust(path: Path) -> ToolResult:
    try:
        rows = list(csv.DictReader(path.read_text(encoding="utf-8", errors="replace").splitlines()))
    except (OSError, csv.Error) as exc:
        raise ReportError(f"can't read {path.name}: {exc}") from exc
    total = next((r for r in rows if (r.get("Name") or "").strip() == "Aggregated"), None)
    _expect(
        total is not None and "Request Count" in total,
        "expected Locust's stats CSV (--csv prefix, the *_stats.csv file)",
    )
    assert total is not None
    p95 = total.get("95%")
    return _load_result(
        requests=float(total.get("Request Count") or 0),
        failures=float(total.get("Failure Count") or 0),
        p95_ms=float(p95) if p95 not in (None, "", "N/A") else None,
        extra={"rps": round(float(total.get("Requests/s") or 0), 1)},
    )


# --- Tests and their quality -----------------------------------------------------


def _xml_root(path: Path) -> ET.Element:
    """Parse an XML report with no DTD or entities (so no entity expansion attacks)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        # Cobertura files may carry a DOCTYPE line; entities are never needed.
        text = re.sub(r"<!DOCTYPE[^>\[]*(\[[^\]]*\])?\s*>", "", text, count=1)
        if "<!ENTITY" in text or "<!DOCTYPE" in text:
            raise ReportError(f"{path.name} declares XML entities, which are not allowed")
    return ET.fromstring(text)  # noqa: S314 - no DTD or entities left (checked above)


def parse_junit(path: Path) -> ToolResult:
    """Pass rate of a JUnit XML report (Schemathesis, any test runner)."""
    try:
        root = _xml_root(path)
    except Exception as exc:  # noqa: BLE001 - any parse error means "not JUnit"
        raise ReportError(f"{path.name} is not JUnit XML: {exc}") from exc
    cases = list(root.iter("testcase"))
    _expect(bool(cases), "expected a JUnit XML report with test cases")
    findings: list[Finding] = []
    skipped = 0
    for case in cases:
        failed = case.find("failure") if case.find("failure") is not None else case.find("error")
        if case.find("skipped") is not None:
            skipped += 1
        elif failed is not None:
            name = f"{case.get('classname', '')}.{case.get('name', '')}".strip(".")
            findings.append(
                Finding(
                    "high",
                    name,
                    _short(failed.get("message") or failed.text),
                    case.get("file") or "",
                )
            )
    ran = len(cases) - skipped
    passed = ran - len(findings)
    score = _clamp(100.0 * passed / ran) if ran else 0
    return ToolResult(
        score=score,
        summary=f"{passed} of {ran} checks passed" + (f", {skipped} skipped" if skipped else ""),
        metrics={"checks": ran, "passed": passed, "failed": len(findings), "skipped": skipped},
        findings=tuple(findings[:MAX_FINDINGS]),
        counts={"high": len(findings)},
    )


def parse_coverage(path: Path) -> ToolResult:
    if path.suffix.lower() == ".xml":
        try:
            root = _xml_root(path)
        except Exception as exc:  # noqa: BLE001
            raise ReportError(f"{path.name} is not Cobertura XML: {exc}") from exc
        rate = root.get("line-rate")
        _expect(rate is not None, "expected a Cobertura XML report (coverage xml) with line-rate")
        pct = float(rate or 0) * 100
    else:
        data = _load_json(path)
        totals = data.get("totals") if isinstance(data, dict) else None
        if isinstance(totals, dict) and "percent_covered" in totals:  # coverage.py json
            pct = float(totals["percent_covered"])
        elif isinstance(data, dict) and isinstance(
            data.get("total"), dict
        ):  # istanbul json-summary
            pct = float(((data["total"].get("lines") or {}).get("pct")) or 0)
        else:
            raise ReportError(
                "expected coverage.py JSON (coverage json), an istanbul json-summary or Cobertura XML"
            )
    return ToolResult(
        score=_clamp(pct),
        summary=f"{pct:.1f}% of lines covered",
        metrics={"line_coverage_pct": round(pct, 1)},
    )


def parse_mutation(path: Path) -> ToolResult:
    """mutmut's ``export-cicd-stats`` JSON, or a Stryker mutation-testing report."""
    data = _load_json(path)
    _expect(isinstance(data, dict), "expected mutmut's cicd stats JSON or a Stryker report")
    if "killed" in data and "survived" in data:
        killed = int(data.get("killed") or 0)
        survived = int(data.get("survived") or 0)
        timeout = int(data.get("timeout") or 0)
        no_tests = int(data.get("no_tests") or 0)
    elif isinstance(data.get("files"), dict):
        statuses = [
            m.get("status") for f in data["files"].values() for m in (f or {}).get("mutants") or []
        ]
        killed = sum(1 for s in statuses if s == "Killed")
        timeout = sum(1 for s in statuses if s == "Timeout")
        survived = sum(1 for s in statuses if s == "Survived")
        no_tests = sum(1 for s in statuses if s == "NoCoverage")
    else:
        raise ReportError(
            "expected mutmut's cicd stats JSON (mutmut export-cicd-stats) or a Stryker JSON report"
        )
    detected = killed + timeout
    total = detected + survived + no_tests
    _expect(total > 0, "the mutation report has no mutants")
    pct = 100.0 * detected / total
    findings = []
    if survived:
        findings.append(
            Finding(
                "medium", "Surviving mutants", f"{survived} changes to the code no test noticed", ""
            )
        )
    if no_tests:
        findings.append(
            Finding("low", "Mutants without tests", f"{no_tests} changes in code no test runs", "")
        )
    return ToolResult(
        score=_clamp(pct),
        summary=f"{detected} of {total} mutants caught ({pct:.0f}%)",
        metrics={"mutants": total, "caught": detected, "survived": survived, "no_tests": no_tests},
        findings=tuple(findings),
        counts={s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES},
    )


PARSERS: dict[str, Callable[[Path], ToolResult]] = {
    "sarif": parse_sarif,
    "bandit": parse_bandit,
    "ruff": parse_ruff,
    "pip-audit": parse_pip_audit,
    "npm-audit": parse_npm_audit,
    "gitleaks": parse_gitleaks,
    "trivy": parse_trivy,
    "openssf-scorecard": parse_openssf_scorecard,
    "zap": parse_zap,
    "axe": parse_axe,
    "lighthouse": parse_lighthouse,
    "k6": parse_k6,
    "locust": parse_locust,
    "junit": parse_junit,
    "coverage": parse_coverage,
    "mutation": parse_mutation,
}
