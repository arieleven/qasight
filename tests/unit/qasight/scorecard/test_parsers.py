"""Each scorecard tool's report parsed into a score and findings (``qasight.services.scorecard.parsers``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from qasight.services.scorecard import parsers
from qasight.services.scorecard.parsers import Finding, ReportError, curve, finding_points
from qasight.services.scorecard.tools import TOOLS, TOOLS_BY_ID

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _json(tmp_path: Path, payload: Any, name: str = "report.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_curve_starts_at_100_and_halves_at_100_points() -> None:
    assert curve(0) == 100
    assert curve(100) == 50
    assert curve(10) == 91
    assert 0 < curve(10_000) < 5


def test_repeats_of_one_rule_cost_less_than_new_rules() -> None:
    same = [Finding("low", "B101")] * 64
    different = [Finding("low", f"R{i}") for i in range(64)]
    assert finding_points(same) == pytest.approx(7.0)
    assert finding_points(different) == pytest.approx(64.0)


def test_every_tool_has_a_parser() -> None:
    assert {tool.parser for tool in TOOLS} <= set(parsers.PARSERS)


def test_bandit(tmp_path: Path) -> None:
    report = {
        "results": [
            {
                "issue_severity": "HIGH",
                "test_id": "B602",
                "test_name": "shell",
                "issue_text": "shell=True",
                "filename": "a.py",
                "line_number": 3,
            },
            {
                "issue_severity": "LOW",
                "test_id": "B101",
                "test_name": "assert",
                "issue_text": "assert",
                "filename": "a.py",
                "line_number": 9,
            },
        ],
        "metrics": {"_totals": {"loc": 120}},
    }
    result = TOOLS_BY_ID["bandit"].parse(_json(tmp_path, report))
    assert result.score == curve(11)
    assert result.summary == "2 issues (1 high, 1 low)"
    assert result.findings[0].severity == "high"
    assert result.findings[0].location == "a.py:3"
    assert result.metrics["lines_scanned"] == 120


def test_clean_report_scores_100(tmp_path: Path) -> None:
    result = TOOLS_BY_ID["bandit"].parse(_json(tmp_path, {"results": []}))
    assert (result.score, result.summary) == (100, "No issues")


def test_sarif_uses_security_severity_then_level(tmp_path: Path) -> None:
    report = {
        "runs": [
            {
                "tool": {
                    "driver": {"rules": [{"id": "sql", "properties": {"security-severity": "9.1"}}]}
                },
                "results": [
                    {
                        "ruleId": "sql",
                        "level": "warning",
                        "message": {"text": "injection"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "db.py"},
                                    "region": {"startLine": 4},
                                }
                            }
                        ],
                    },
                    {"ruleId": "style", "level": "note", "message": {"text": "nit"}},
                ],
            }
        ]
    }
    result = TOOLS_BY_ID["semgrep"].parse(_json(tmp_path, report, "r.sarif"))
    assert [f.severity for f in result.findings] == ["critical", "low"]
    assert result.findings[0].location == "db.py:4"


def test_pip_audit_counts_each_vulnerability(tmp_path: Path) -> None:
    report = {
        "dependencies": [
            {
                "name": "jinja2",
                "version": "2.0",
                "vulns": [{"id": "GHSA-1", "fix_versions": ["3.1"]}],
            },
            {"name": "ok", "version": "1", "vulns": []},
        ]
    }
    result = TOOLS_BY_ID["pip-audit"].parse(_json(tmp_path, report))
    assert result.counts["high"] == 1
    assert "fixed in 3.1" in result.findings[0].detail
    assert result.metrics["packages"] == 2


def test_npm_audit(tmp_path: Path) -> None:
    report = {
        "vulnerabilities": {
            "lodash": {"severity": "critical", "via": [{"title": "Prototype pollution"}]}
        }
    }
    result = TOOLS_BY_ID["npm-audit"].parse(_json(tmp_path, report))
    assert result.findings[0].severity == "critical"


def test_gitleaks_secret_is_critical(tmp_path: Path) -> None:
    result = TOOLS_BY_ID["gitleaks"].parse(
        _json(tmp_path, [{"RuleID": "aws-key", "File": "x.env", "StartLine": 2}])
    )
    assert result.score == 80


def test_trivy_json(tmp_path: Path) -> None:
    report = {
        "SchemaVersion": 2,
        "Results": [
            {
                "Target": "requirements.txt",
                "Vulnerabilities": [
                    {"Severity": "HIGH", "PkgName": "x", "VulnerabilityID": "CVE-1"}
                ],
            },
            {
                "Target": "Dockerfile",
                "Misconfigurations": [
                    {"ID": "DS001", "Severity": "LOW", "Status": "FAIL"},
                    {"ID": "DS002", "Status": "PASS"},
                ],
            },
        ],
    }
    result = TOOLS_BY_ID["trivy"].parse(_json(tmp_path, report))
    assert sorted(f.severity for f in result.findings) == ["high", "low"]


def test_openssf_scorecard_is_its_score_times_ten(tmp_path: Path) -> None:
    report = {
        "score": 6.4,
        "checks": [
            {"name": "Pinned-Dependencies", "score": 2, "reason": "unpinned"},
            {"name": "License", "score": 10},
            {"name": "Fuzzing", "score": -1},
        ],
    }
    result = TOOLS_BY_ID["openssf-scorecard"].parse(_json(tmp_path, report))
    assert result.score == 64
    assert [f.title for f in result.findings] == ["Pinned-Dependencies"]


def test_zap_risk_codes(tmp_path: Path) -> None:
    report = {
        "site": [
            {
                "alerts": [
                    {"alert": "CSP missing", "riskcode": "2", "instances": [{"uri": "http://x/"}]},
                    {"alert": "Info", "riskcode": "0"},
                ]
            }
        ]
    }
    result = TOOLS_BY_ID["zap"].parse(_json(tmp_path, report))
    assert [f.severity for f in result.findings] == ["medium", "info"]


def test_axe_impact(tmp_path: Path) -> None:
    report = [
        {
            "url": "http://x/",
            "violations": [
                {"id": "color-contrast", "impact": "serious", "help": "Contrast", "nodes": [{}, {}]}
            ],
        }
    ]
    result = TOOLS_BY_ID["axe"].parse(_json(tmp_path, report))
    assert result.findings[0].severity == "high"
    assert "2 elements" in result.findings[0].detail


def test_lighthouse_is_the_mean_of_categories(tmp_path: Path) -> None:
    report = {"categories": {"performance": {"score": 0.6}, "accessibility": {"score": 1.0}}}
    result = TOOLS_BY_ID["lighthouse"].parse(_json(tmp_path, report))
    assert result.score == 80
    assert result.metrics == {"performance": 60, "accessibility": 100}


def test_k6_thresholds_decide_when_present(tmp_path: Path) -> None:
    report = {
        "metrics": {
            "http_reqs": {"count": 200},
            "http_req_failed": {"value": 0.0},
            "http_req_duration": {"p(95)": 120.0, "thresholds": {"p(95)<100": True}},
            "checks": {"thresholds": {"rate>0.99": False}},
        }
    }
    result = TOOLS_BY_ID["k6"].parse(_json(tmp_path, report))
    assert result.score == 50
    assert result.metrics["thresholds"] == 2


def test_k6_without_thresholds_scores_errors_and_latency(tmp_path: Path) -> None:
    report = {
        "metrics": {
            "http_reqs": {"count": 100},
            "http_req_failed": {"value": 0.1},
            "http_req_duration": {"p(95)": 400},
        }
    }
    assert TOOLS_BY_ID["k6"].parse(_json(tmp_path, report)).score == 90


def test_locust_csv(tmp_path: Path) -> None:
    path = tmp_path / "run_stats.csv"
    path.write_text(
        "Type,Name,Request Count,Failure Count,95%,Requests/s\nGET,/a,10,0,100,1\n,Aggregated,100,0,3500,10\n",
        encoding="utf-8",
    )
    result = TOOLS_BY_ID["locust"].parse(path)
    assert result.score == 50
    assert result.metrics["p95_ms"] == 3500.0


def test_junit_pass_rate(tmp_path: Path) -> None:
    path = tmp_path / "r.xml"
    path.write_text(
        '<testsuite><testcase name="a"/><testcase name="b"><failure message="500"/></testcase><testcase name="c"><skipped/></testcase></testsuite>',
        encoding="utf-8",
    )
    result = TOOLS_BY_ID["schemathesis"].parse(path)
    assert result.score == 50
    assert result.summary == "1 of 2 checks passed, 1 skipped"


def test_coverage_formats(tmp_path: Path) -> None:
    assert (
        TOOLS_BY_ID["coverage"].parse(_json(tmp_path, {"totals": {"percent_covered": 81.4}})).score
        == 81
    )
    assert (
        TOOLS_BY_ID["coverage"]
        .parse(_json(tmp_path, {"total": {"lines": {"pct": 70}}}, "s.json"))
        .score
        == 70
    )
    xml = tmp_path / "coverage.xml"
    xml.write_text('<coverage line-rate="0.5"></coverage>', encoding="utf-8")
    assert TOOLS_BY_ID["coverage"].parse(xml).score == 50


def test_mutation_formats(tmp_path: Path) -> None:
    mutmut = {"killed": 60, "survived": 30, "timeout": 8, "no_tests": 2}
    assert TOOLS_BY_ID["mutation"].parse(_json(tmp_path, mutmut)).score == 68
    stryker = {"files": {"a.js": {"mutants": [{"status": "Killed"}, {"status": "Survived"}]}}}
    assert TOOLS_BY_ID["mutation"].parse(_json(tmp_path, stryker, "s.json")).score == 50


@pytest.mark.parametrize(
    "tool",
    ["bandit", "pip-audit", "gitleaks", "zap", "lighthouse", "k6", "mutation", "openssf-scorecard"],
)
def test_wrong_file_says_what_was_expected(tmp_path: Path, tool: str) -> None:
    with pytest.raises(ReportError, match="expected"):
        TOOLS_BY_ID[tool].parse(_json(tmp_path, {"something": "else"}))


def test_empty_and_non_json_files(tmp_path: Path) -> None:
    empty = tmp_path / "e.json"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ReportError, match="empty"):
        TOOLS_BY_ID["bandit"].parse(empty)
    junk = tmp_path / "j.json"
    junk.write_text("not json", encoding="utf-8")
    with pytest.raises(ReportError, match="not JSON"):
        TOOLS_BY_ID["bandit"].parse(junk)


def test_banner_before_json_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text('Run started\n{"results": []}', encoding="utf-8")
    assert TOOLS_BY_ID["bandit"].parse(path).score == 100


def test_xml_reports_with_entities_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "r.xml"
    path.write_text(
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
        '<testsuite><testcase name="&a;"/></testsuite>',
        encoding="utf-8",
    )
    with pytest.raises(ReportError):
        TOOLS_BY_ID["schemathesis"].parse(path)
