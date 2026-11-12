"""The scorecard's fixes from its first run on outside repositories (CIViC, cBioPortal).

Coverage that caps the grade, gitleaks noise, why a tool failed, the ignore
file, the "Not run" list and the history database.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.repository.db_config import reset_engine_cache
from qasight.services.scorecard.ignore import (
    IgnoreFileError,
    apply_ignores,
    load_ignores,
)
from qasight.services.scorecard.parsers import ToolResult, is_test_path, plural
from qasight.services.scorecard.report import (
    build_scorecard,
    capped_grade,
    coverage_of,
    to_markdown,
)
from qasight.services.scorecard.scan import resolve_target, run_scan
from qasight.services.scorecard.store import ScorecardStore, StoredCoverage
from qasight.services.scorecard.tools import TOOLS_BY_ID

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

runner = CliRunner()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ScorecardStore]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    reset_engine_cache()
    yield ScorecardStore()
    reset_engine_cache()


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _json(path: Path, payload: Any) -> Path:
    return _write(path, json.dumps(payload))


def _fake_tool(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# --- 2. coverage caps the grade --------------------------------------------------


def test_grade_is_capped_when_under_half_the_tools_ran() -> None:
    few = coverage_of(
        StoredCoverage(expected=["bandit", "semgrep", "ruff"], missing={}), {"bandit"}
    )
    assert few is not None and (few.ran, few.expected) == (1, 3)
    assert capped_grade(98, few) == ("C", "A")
    assert capped_grade(50, few) == ("F", None)
    most = coverage_of(StoredCoverage(expected=["bandit", "ruff"], missing={}), {"bandit"})
    assert capped_grade(98, most) == ("A", None)
    assert capped_grade(98, None) == ("A", None)


def test_scorecard_carries_coverage(store: ScorecardStore) -> None:
    store.record(project="p", version="1", tool="bandit", result=ToolResult(98, "ok"))
    store.record_coverage(
        project="p",
        version="1",
        expected=["bandit", "semgrep", "gitleaks"],
        missing={"semgrep": "failed: offline", "gitleaks": "not installed"},
    )
    card = build_scorecard("p", "1", store=store)
    assert card is not None and card.score == 98 and card.grade == "C"
    assert card.coverage is not None and card.coverage.capped_from == "A"
    assert card.versions[0].grade == "C"
    markdown = to_markdown(card)
    assert "1 of 3 tools that fit this repository ran" in markdown
    assert "- Semgrep: failed: offline" in markdown


# --- 3. gitleaks ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "test"),
    [
        ("src/test/resources/x.json", True),
        ("src/shared/expressionTestData.json", True),
        ("app/keys.spec.ts", True),
        ("a/__mocks__/b.js", True),
        ("test_keys.py", True),
        ("a/latest.json", False),
        ("docs/contest.md", False),
        ("src/main/resources/dev/key.pem", False),
    ],
)
def test_test_paths(path: str, test: bool) -> None:
    assert is_test_path(path) is test


def test_gitleaks_skips_fixtures_dedupes_and_ranks_rules(tmp_path: Path) -> None:
    leak = {"RuleID": "generic-api-key", "Secret": "abc", "File": "src/a.ts", "StartLine": 1}
    report = [
        leak,
        {**leak, "File": "src/b.ts"},  # the same secret again
        {**leak, "File": "src/expressionTestData.json"},
        {"RuleID": "private-key", "Secret": "k", "File": "deploy/id_rsa", "StartLine": 1},
    ]
    result = TOOLS_BY_ID["gitleaks"].parse(_json(tmp_path / "r.json", report))
    assert result.metrics["findings"] == 2
    assert result.counts["medium"] == 1 and result.counts["critical"] == 1
    assert result.metrics["in_test_files"] == 1
    assert "1 in test or fixture files not counted" in result.summary


# --- 4. why a tool failed -----------------------------------------------------------


def test_a_failed_tool_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_tool(
        bin_dir,
        "semgrep",
        "echo 'Scanning...' >&2; echo 'Failed to download configuration from https://semgrep.dev' >&2; exit 2",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    repo = _write(tmp_path / "repo" / "a.py").parent
    outcome = run_scan(
        resolve_target(str(repo), project="p", version="1"),
        out_dir=tmp_path / "out",
        enabled={},
        record=lambda *_: None,
        only=["semgrep"],
    )
    message = outcome.errors["semgrep"]
    assert "Failed to download configuration" in message and "needs network" in message


# --- 9. ignore file -------------------------------------------------------------------


def _zizmor_sarif(path: Path) -> Path:
    return _json(
        path,
        {
            "runs": [
                {
                    "results": [
                        {
                            "ruleId": "dangerous-triggers",
                            "level": "error",
                            "message": {"text": "pull_request_target"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": ".github/workflows/release-management.yml"
                                        },
                                        "region": {"startLine": 3},
                                    }
                                }
                            ],
                        }
                    ]
                }
            ]
        },
    )


def test_ignore_file_drops_accepted_findings_and_rescores(tmp_path: Path) -> None:
    rules = load_ignores(
        _write(
            tmp_path / "scorecard-ignore.yml",
            "ignore:\n"
            "  - tool: zizmor\n"
            "    rule: dangerous-triggers\n"
            "    path: .github/workflows/release-*.yml\n"
            "    reason: checks nothing out\n",
        )
    )
    result = TOOLS_BY_ID["zizmor"].parse(_zizmor_sarif(tmp_path / "z.sarif"))
    assert result.score < 100
    kept = apply_ignores("zizmor", result, rules)
    assert kept.score == 100 and kept.metrics["ignored"] == 1 and "1 ignored" in kept.summary
    assert apply_ignores("bandit", result, rules) is result


@pytest.mark.parametrize(
    "text",
    ["ignore: nope\n", "ignore:\n  - tool: nope\n    rule: x\n", "ignore:\n  - tool: bandit\n"],
)
def test_bad_ignore_files(tmp_path: Path, text: str) -> None:
    with pytest.raises(IgnoreFileError):
        load_ignores(_write(tmp_path / "i.yml", text))


# --- the CLI: one "Not run" list, the history file, coverage --------------------


@pytest.fixture
def cli_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db = tmp_path / "history.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    for name in ("TESTO_SERVER_URL", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    reset_engine_cache()
    yield db
    reset_engine_cache()


def test_scan_lists_each_missing_tool_once_and_names_the_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_history: Path
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bandit_report = json.dumps({"results": []}).replace('"', '\\"')
    _fake_tool(
        bin_dir, "bandit", f'while [ "$1" != "-o" ]; do shift; done; echo "{bandit_report}" > "$2"'
    )
    _fake_tool(bin_dir, "semgrep", "echo 'Failed to download config' >&2; exit 2")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    repo = _write(tmp_path / "repo" / "a.py").parent
    result = runner.invoke(
        app,
        [
            "scorecard",
            "scan",
            str(repo),
            "--version",
            "1",
            "--tools",
            "bandit,semgrep,flawfinder",
            "--out",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Failed to download config") == 1
    assert "Semgrep: failed\n" in result.output
    assert f"History: {cli_history}" in result.output
    # Flawfinder doesn't fit (no C code), so 1 of the 2 that fit ran: the grade is capped.
    assert "1 of 2 tools that fit this repository ran." in result.output


def test_summaries_say_dependencies_not_dependencys() -> None:
    assert plural(2, "vulnerable dependency") == "2 vulnerable dependencies"
    assert plural(1, "vulnerable dependency") == "1 vulnerable dependency"
    assert plural(3, "key") == "3 keys"
