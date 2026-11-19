"""The opt-in scanners added after CodeQL: Nuclei, TruffleHog and Claude's security review."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from qasight.services.scorecard.parsers import (
    ReportError,
    parse_claude_security_review,
    parse_nuclei,
    parse_trufflehog,
)
from qasight.services.scorecard.scan import (
    ScanError,
    checked_diff_base,
    plan_tools,
    resolve_target,
    run_scan,
)
from qasight.services.scorecard.tools import TOOLS_BY_ID, ScanContext

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

NEW = ("nuclei", "trufflehog", "claude-security-review")


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _nuclei_row(template: str, severity: str, at: str, matcher: str = "") -> str:
    row = {
        "template-id": template,
        "info": {"name": template.replace("-", " ").title(), "severity": severity},
        "matcher-name": matcher,
        "type": "http",
        "host": "http://localhost:8000",
        "matched-at": at,
    }
    return json.dumps(row)


def test_new_scanners_are_off_by_default_and_listed() -> None:
    for tool_id in NEW:
        tool = TOOLS_BY_ID[tool_id]
        assert not tool.default_on and tool.category == "security"
    assert TOOLS_BY_ID["nuclei"].kind == "app"
    run, skipped = plan_tools(languages={"python"}, enabled={})
    for tool_id in NEW:
        assert skipped[tool_id] == "switched off"
    assert not {t.id for t in run} & set(NEW)


def test_nuclei_needs_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/bin/x" if name == "nuclei" else None)
    _, skipped = plan_tools(languages=set(), enabled={"nuclei": True})
    assert skipped["nuclei"] == "needs a running app (--url)"
    run, _ = plan_tools(languages=set(), enabled={"nuclei": True}, url="http://localhost:1")
    assert "nuclei" in [t.id for t in run]
    tool = TOOLS_BY_ID["nuclei"]
    assert tool.command is not None
    cmd = tool.command(ScanContext(Path("."), Path("r.json"), url="http://localhost:1"))
    assert cmd is not None and cmd.empty_ok and cmd.stdout_is_report
    assert "dos,fuzz,intrusive" in cmd.argv and "-jsonl" in cmd.argv
    assert tool.command(ScanContext(Path("."), Path("r.json"))) is None


def test_parse_nuclei_scores_each_match_by_its_template_severity(tmp_path: Path) -> None:
    lines = [
        "[INF] a log line nuclei printed",
        _nuclei_row("git-config", "medium", "http://localhost:8000/.git/config"),
        _nuclei_row("git-config", "medium", "http://localhost:8000/.git/config"),  # repeat
        _nuclei_row("tech-detect", "info", "http://localhost:8000/", "fastapi"),
        _nuclei_row("CVE-2021-0000", "critical", "http://localhost:8000/admin"),
    ]
    result = parse_nuclei(_write(tmp_path / "r.json", "\n".join(lines)))
    assert result.counts["critical"] == 1 and result.counts["medium"] == 1
    assert result.counts["info"] == 1
    assert result.findings[0].location == "http://localhost:8000/admin"
    assert result.score < 80
    # -json-export writes one array instead.
    array = "[" + ",".join(lines[1:]) + "]"
    assert parse_nuclei(_write(tmp_path / "a.json", array)).counts == result.counts


def test_parse_nuclei_empty_is_clean_and_other_json_is_an_error(tmp_path: Path) -> None:
    clean = parse_nuclei(_write(tmp_path / "empty.json", ""))
    assert clean.score == 100 and clean.summary == "No templates matched"
    with pytest.raises(ReportError, match="template-id"):
        parse_nuclei(_write(tmp_path / "zap.json", '{"site": []}'))


def _th_row(detector: str, raw: str, file: str, *, verified: bool, error: str = "") -> str:
    row = {
        "SourceMetadata": {"Data": {"Filesystem": {"file": file, "line": 3}}},
        "SourceName": "trufflehog - filesystem",
        "DetectorName": detector,
        "Verified": verified,
        "Raw": raw,
    }
    if error:
        row["VerificationError"] = error
    return json.dumps(row)


def test_parse_trufflehog_ranks_live_secrets_highest(tmp_path: Path) -> None:
    lines = [
        _th_row("AWS", "AKIA1", "app/settings.py", verified=True),
        _th_row("AWS", "AKIA1", "app/other.py", verified=True),  # same secret twice
        _th_row("Slack", "xoxb-1", "app/bot.py", verified=False, error="timeout"),
        _th_row("Generic", "abc", "app/util.py", verified=False),
        _th_row("Stripe", "sk_test", "tests/test_pay.py", verified=True),
    ]
    result = parse_trufflehog(_write(tmp_path / "r.json", "\n".join(lines)))
    assert result.counts["critical"] == 1
    assert result.counts["high"] == 1 and result.counts["medium"] == 1
    assert result.metrics["live"] == 1 and result.metrics["in_test_files"] == 1
    assert result.findings[0].title == "AWS secret"
    assert result.findings[0].location == "app/settings.py:3"
    assert "test or fixture" in result.summary
    assert parse_trufflehog(_write(tmp_path / "e.json", "")).score == 100


def test_trufflehog_skips_vendored_folders(tmp_path: Path) -> None:
    tool = TOOLS_BY_ID["trufflehog"]
    assert tool.command is not None
    cmd = tool.command(ScanContext(tmp_path, tmp_path / "out" / "report.json"))
    assert cmd is not None and cmd.empty_ok
    assert cmd.argv[:3] == ["trufflehog", "filesystem", "."] and "--json" in cmd.argv
    (exclude,) = cmd.files
    assert "node_modules" in cmd.files[exclude] and str(exclude) in cmd.argv


def _claude_envelope(answer: str, **extra: object) -> str:
    return json.dumps(
        {"type": "result", "subtype": "success", "is_error": False, "result": answer, **extra}
    )


def test_parse_claude_review_reads_the_answer_inside_claude_codes_json(tmp_path: Path) -> None:
    answer = (
        "Here is what I found:\n```json\n"
        + json.dumps(
            {
                "findings": [
                    {
                        "file": "app/db.py",
                        "line": 12,
                        "severity": "HIGH",
                        "category": "sql_injection",
                        "description": "User input is formatted into the query.",
                        "recommendation": "Use bound parameters.",
                        "confidence": 0.95,
                    },
                    {"file": "app/web.py", "severity": "LOW", "category": "open_redirect"},
                ]
            }
        )
        + "\n```"
    )
    path = _write(tmp_path / "r.json", _claude_envelope(answer, total_cost_usd=0.1234567))
    result = parse_claude_security_review(path)
    assert result.counts["high"] == 1 and result.counts["low"] == 1
    first = result.findings[0]
    assert first.title == "sql injection" and first.location == "app/db.py:12"
    assert "bound parameters" in first.detail
    assert result.metrics["cost_usd"] == 0.1235


def test_parse_claude_review_takes_the_actions_results_file(tmp_path: Path) -> None:
    data = {"pr_number": 7, "findings": [{"file": "a.py", "line": 1, "severity": "MEDIUM"}]}
    result = parse_claude_security_review(_write(tmp_path / "r.json", json.dumps(data)))
    assert result.counts["medium"] == 1
    clean = parse_claude_security_review(
        _write(tmp_path / "c.json", _claude_envelope('{"findings": []}'))
    )
    assert clean.score == 100 and clean.summary == "No security findings"


def test_parse_claude_review_says_why_claude_stopped(tmp_path: Path) -> None:
    stopped = json.dumps({"type": "result", "is_error": True, "result": "Invalid API key"})
    with pytest.raises(ReportError, match="Invalid API key"):
        parse_claude_security_review(_write(tmp_path / "e.json", stopped))
    with pytest.raises(ReportError, match="no JSON findings"):
        parse_claude_security_review(_write(tmp_path / "n.json", _claude_envelope("Looks fine")))


def test_claude_review_is_read_only_and_follows_diff_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = TOOLS_BY_ID["claude-security-review"]
    assert tool.command is not None
    monkeypatch.delenv("QASIGHT_CLAUDE_MODEL", raising=False)
    whole = tool.command(ScanContext(tmp_path, tmp_path / "r.json"))
    assert whole is not None and whole.argv[:2] == ["claude", "-p"]
    assert "Review the code in this repository" in whole.argv[2]
    tools = whole.argv[whole.argv.index("--allowedTools") + 1]
    assert "Edit" not in tools and "Write" not in tools and "Bash(git diff:*)" in tools
    assert "--model" not in whole.argv
    monkeypatch.setenv("QASIGHT_CLAUDE_MODEL", "claude-sonnet-5-5")
    diff = tool.command(ScanContext(tmp_path, tmp_path / "r.json", diff_base="origin/main"))
    assert diff is not None and "git diff origin/main...HEAD" in diff.argv[2]
    assert diff.argv[-2:] == ["--model", "claude-sonnet-5-5"]


@pytest.mark.parametrize("ref", ["origin/main", "v1.6.0", "HEAD~3", "abc123", "release/1.7"])
def test_diff_base_takes_git_refs(ref: str) -> None:
    assert checked_diff_base(ref) == ref


@pytest.mark.parametrize("ref", ["--output=x", "main..HEAD", "a b", "x;rm", "$(id)"])
def test_diff_base_refuses_anything_else(ref: str) -> None:
    with pytest.raises(ScanError, match="not a git ref"):
        checked_diff_base(ref)


def _fake_tool(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_scan_runs_the_new_scanners_and_a_clean_run_scores_100(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # TruffleHog and Nuclei find nothing and print nothing; Claude answers with one finding.
    _fake_tool(bin_dir, "trufflehog", "exit 0")
    _fake_tool(bin_dir, "nuclei", "exit 0")
    answer = json.dumps({"findings": [{"file": "a.py", "line": 1, "severity": "HIGH"}]})
    envelope = _claude_envelope(answer).replace("\\", "\\\\").replace('"', '\\"')
    _fake_tool(bin_dir, "claude", f'echo "{envelope}"')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("print(1)\n", encoding="utf-8")
    recorded: dict[str, int] = {}
    outcome = run_scan(
        resolve_target(str(repo), project="p", version="1"),
        out_dir=tmp_path / "out",
        enabled={},
        record=lambda tool, result: recorded.__setitem__(tool, result.score),
        only=list(NEW),
        url="http://localhost:1",
        diff_base="origin/main",
    )
    assert outcome.errors == {}
    assert recorded["trufflehog"] == 100 and recorded["nuclei"] == 100
    assert recorded["claude-security-review"] == 91


def test_an_empty_report_is_still_an_error_when_the_tool_failed(tmp_path: Path) -> None:
    from dataclasses import replace

    from qasight.services.scorecard.scan import run_tool
    from qasight.services.scorecard.tools import Command

    tool = replace(
        TOOLS_BY_ID["trufflehog"],
        command=lambda ctx: Command(
            ["sh", "-c", "echo bad flag >&2; exit 2"],
            ctx.repo,
            stdout_is_report=True,
            empty_ok=True,
        ),
    )
    with pytest.raises(ReportError, match="bad flag"):
        run_tool(tool, ScanContext(tmp_path, tmp_path / "report.json"))
