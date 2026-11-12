"""MegaLinter as the engine: which image, how its reports become one result per linter, and the scoring."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from qasight.repository.db_config import reset_engine_cache
from qasight.services.scorecard import megalinter
from qasight.services.scorecard.parsers import Finding, ToolResult
from qasight.services.scorecard.paths import classify, profile
from qasight.services.scorecard.rating import density_score, findings_score, worst_finding_score
from qasight.services.scorecard.report import build_scorecard, gate_failures, to_markdown
from qasight.services.scorecard.scan import resolve_target, run_scan
from qasight.services.scorecard.store import ScorecardError, ScorecardStore

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ScorecardStore]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    reset_engine_cache()
    yield ScorecardStore()
    reset_engine_cache()


# --- what a path is, and the repository's size ---------------------------------


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("src/app/main.py", "source"),
        ("/tmp/lint/src/app/main.py:12", "source"),
        ("file:///tmp/lint/lib/user.rb", "source"),
        ("tests/test_api.py", "test"),
        ("src/test/java/org/x/UserTest.java", "test"),
        ("web/src/app.spec.ts", "test"),
        ("spec/models/user_spec.rb", "test"),
        ("vendor/github.com/x/y.go", "vendor"),
        ("third_party/zlib/inflate.c", "vendor"),
        ("frontend/dist/app.min.js", "generated"),
        ("api/v1_pb2.py", "generated"),
        ("docs/guide.md", "docs"),
        ("frontend/package-lock.json", "source"),
        ("requirements.txt", "source"),
        (".github/workflows/ci.yml", "source"),
    ],
)
def test_classify(path: str, kind: str) -> None:
    assert classify(path) == kind


def test_profile_counts_source_lines_by_language(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text("class A {\n\n  int x;\n}\n", encoding="utf-8")
    (tmp_path / "src" / "b.rb").write_text("puts 1\n", encoding="utf-8")
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "ATest.java").write_text("x\n" * 50, encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "z.js").write_text("x\n" * 50, encoding="utf-8")
    found = profile(tmp_path)
    assert found.lines == {"java": 3, "ruby": 1}
    assert found.main_language() == "java"
    assert megalinter.image_for(found.main_language()) == "ghcr.io/oxsecurity/megalinter-java:v9"
    assert megalinter.image_for(None) == "ghcr.io/oxsecurity/megalinter:v9"
    assert megalinter.image_for("java", "me/linter:1") == "me/linter:1"


# --- rating ----------------------------------------------------------------------


def test_security_goes_by_the_worst_finding_and_code_by_density() -> None:
    assert worst_finding_score({}) == 100
    assert worst_finding_score({"low": 1}) == 89
    assert worst_finding_score({"medium": 1, "low": 30}) == 79
    assert worst_finding_score({"high": 2}) == 67
    assert worst_finding_score({"critical": 1}) == 59
    # 1,000 low issues in 1M lines is as good as 1 in 1,000.
    assert density_score({"low": 1000}, 1_000_000) == density_score({"low": 1}, 1000) == 98
    assert density_score({"low": 400}, 10_000) == 63
    assert density_score({"high": 500}, 10_000) == 12
    assert findings_score("supply-chain", {"high": 1}, 10) == 69
    assert findings_score("code", {"high": 1}, 10) == 80


# --- reading MegaLinter's reports ------------------------------------------------


def _sarif(results: list[dict]) -> dict:
    return {
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "x", "rules": []}}, "results": results}],
    }


def _result(rule: str, level: str, uri: str, line: int = 1, **props: object) -> dict:
    out: dict = {
        "ruleId": rule,
        "level": level,
        "message": {"text": f"{rule} here"},
        "locations": [
            {"physicalLocation": {"artifactLocation": {"uri": uri}, "region": {"startLine": line}}}
        ],
    }
    if props:
        out["properties"] = props
    return out


def write_reports(out: Path) -> None:
    (out / "sarif").mkdir(parents=True)
    (out / "linters_logs").mkdir()
    linters = [
        ("PYTHON_RUFF", "ruff", "error", 3, 0),
        ("REPOSITORY_GITLEAKS", "gitleaks", "error", 2, 0),
        ("REPOSITORY_GRYPE", "grype", "error", 1, 1),
        ("YAML_YAMLLINT", "yamllint", "error", 4, 1),
        ("REPOSITORY_SYFT", "syft", "success", 0, 0),
        ("SPELL_CSPELL", "cspell", "error", 99, 0),
        ("JAVA_PMD", "pmd", "skipped", 0, 0),
    ]
    report = {
        "linters": [
            {
                "name": key,
                "linter_name": name,
                "status": status,
                "total_number_errors": errors,
                "total_number_warnings": warnings,
                "is_active": True,
            }
            for key, name, status, errors, warnings in linters
        ]
    }
    (out / "mega-linter-report.json").write_text(json.dumps(report), encoding="utf-8")
    (out / "sarif" / "PYTHON_RUFF.sarif").write_text(
        json.dumps(
            _sarif(
                [
                    _result("F401", "error", "file:///tmp/lint/app/main.py", 3),
                    _result("E501", "warning", "app/main.py", 9),
                    _result("F401", "error", "tests/test_main.py", 1),
                ]
            )
        ),
        encoding="utf-8",
    )
    (out / "sarif" / "REPOSITORY_GITLEAKS.sarif").write_text(
        json.dumps(
            _sarif(
                [
                    _result(
                        "aws-key", "error", "config/prod.env", 2, **{"security-severity": "9.5"}
                    ),
                    _result("aws-key", "error", "tests/fixtures/fake.env", 1),
                ]
            )
        ),
        encoding="utf-8",
    )
    (out / "linters_logs" / "REPOSITORY_GRYPE-ERROR.log").write_text(
        "[0000] ERROR failed to load vulnerability db: database does not exist\n", encoding="utf-8"
    )


def test_reports_become_one_result_per_linter(tmp_path: Path) -> None:
    write_reports(tmp_path)
    runs = {r.key: r for r in megalinter.read_reports(tmp_path)}
    # SBOM, spelling and skipped linters report nothing to score.
    assert set(runs) == {"PYTHON_RUFF", "REPOSITORY_GITLEAKS", "REPOSITORY_GRYPE", "YAML_YAMLLINT"}

    ruff = runs["PYTHON_RUFF"]
    assert (ruff.name, ruff.category) == ("ruff (Python)", "code")
    assert ruff.result is not None
    # A lint error is a low issue, a warning info; the one in tests/ isn't counted.
    assert {k: v for k, v in ruff.result.counts.items() if v} == {"low": 1, "info": 1}
    assert ruff.result.metrics["not_counted"] == 1
    assert ruff.result.findings[0].location == "app/main.py:3"
    assert len(ruff.result.fingerprints) == 2

    leaks = runs["REPOSITORY_GITLEAKS"]
    assert leaks.category == "security" and leaks.result is not None
    assert leaks.result.counts["critical"] == 1  # security-severity 9.5; the fixture isn't counted

    grype = runs["REPOSITORY_GRYPE"]
    assert grype.category == "supply-chain" and grype.result is None
    assert "database does not exist" in (grype.error or "")

    yamllint = runs["YAML_YAMLLINT"]
    assert yamllint.result is not None and yamllint.result.metrics["details"] == "log only"
    assert {k: v for k, v in yamllint.result.counts.items() if v} == {"low": 4, "info": 1}


def test_command_keeps_the_repos_config_and_only_moves_the_reports(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    argv = megalinter.command(repo, tmp_path / "out", image="img:1", disabled=["PYTHON_PYLINT"])
    joined = " ".join(argv)
    assert argv[-1] == "img:1"
    assert f"{repo}:/tmp/lint" in joined and "REPORT_OUTPUT_FOLDER=/tmp/reports" in joined
    assert "APPLY_FIXES=none" in joined and "DISABLE=SPELL" in joined
    assert "PYTHON_PYLINT" in joined and "REPOSITORY_SYFT" in joined
    (repo / ".mega-linter.yml").write_text("DISABLE: []\n", encoding="utf-8")
    joined = " ".join(megalinter.command(repo, tmp_path / "out", image="img:1"))
    assert "DISABLE=SPELL" not in joined and "DISABLE_LINTERS" not in joined


def test_command_lints_source_only_reads_pyproject_and_keeps_trufflehog_off(tmp_path: Path) -> None:
    import re

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 100\n[tool.mypy.overrides]\nx = 1\n[tool.black]\n",
        encoding="utf-8",
    )
    argv = megalinter.command(repo, tmp_path / "out", image="img:1")
    env = dict(a.split("=", 1) for a in argv if "=" in a and a[:1].isupper())
    assert "REPOSITORY_TRUFFLEHOG" in env["DISABLE_LINTERS"]
    assert env["PYTHON_RUFF_CONFIG_FILE"] == env["PYTHON_MYPY_CONFIG_FILE"] == "pyproject.toml"
    assert "PYTHON_PYLINT_CONFIG_FILE" not in env
    exclude = re.compile(env["FILTER_REGEX_EXCLUDE"])
    for skipped in ("/tmp/lint/docs/.obsidian/themes/x/theme.css", "tests/test_a.py", "README.md"):
        assert exclude.search(skipped), skipped
    for kept in ("/tmp/lint/app/main.py", "frontend/src/styles.css", "pyproject.toml"):
        assert not exclude.search(kept), kept


def test_duplicate_linters_on_default_rules_are_advisory(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (out / "linters_logs").mkdir(parents=True)
    linters = [
        ("PYTHON_RUFF", "ruff", "error", 1, 0),
        ("PYTHON_FLAKE8", "flake8", "error", 4503, 0),
        ("PYTHON_ISORT", "isort", "warning", 423, 0),
        ("CSS_STYLELINT", "stylelint", "error", 7, 0),
    ]
    report = {
        "linters": [
            {
                "name": k,
                "linter_name": n,
                "status": st,
                "total_number_errors": e,
                "total_number_warnings": w,
                "is_active": True,
            }
            for k, n, st, e, w in linters
        ]
    }
    (out / "mega-linter-report.json").write_text(json.dumps(report), encoding="utf-8")
    (out / "megalinter.log").write_text(
        "- MegaLinter key: [PYTHON_FLAKE8]\n- Rules config: [.flake8]\n"
        "- MegaLinter key: [PYTHON_RUFF]\n- Rules config: [/pyproject.toml]\n"
        "- MegaLinter key: [PYTHON_ISORT]\n- Rules config: [.isort.cfg]\n"
        "- MegaLinter key: [CSS_STYLELINT]\n- Rules config: [.stylelintrc.json]\n",
        encoding="utf-8",
    )
    runs = {r.key: r for r in megalinter.read_reports(out)}
    assert runs["PYTHON_RUFF"].configured and not runs["PYTHON_FLAKE8"].configured
    flake8 = runs["PYTHON_FLAKE8"].result
    assert flake8 is not None and flake8.metrics.get("advisory")
    assert "the project uses ruff (Python)" in flake8.summary
    ruff = runs["PYTHON_RUFF"].result
    assert ruff is not None and not ruff.metrics.get("advisory")
    # No CSS linter is configured, so stylelint's defaults are all there is: scored.
    stylelint = runs["CSS_STYLELINT"].result
    assert stylelint is not None and not stylelint.metrics.get("advisory")
    # isort is non-blocking in MegaLinter: its issues weigh as info.
    isort = runs["PYTHON_ISORT"].result
    assert isort is not None and isort.counts == {"info": 423}


# --- a scan with the engine, stored and scored ----------------------------------


def test_scan_runs_the_engine_skips_what_it_covers_and_scores_by_density(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: ScorecardStore
) -> None:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("x = 1\n" * 2000, encoding="utf-8")

    def fake_run(repo_path: Path, out: Path, **kwargs: object) -> megalinter.EngineOutcome:
        write_reports(out)
        return megalinter.EngineOutcome(
            image=str(kwargs.get("image")), runs=megalinter.read_reports(out)
        )

    monkeypatch.setattr(megalinter, "run", fake_run)
    monkeypatch.setattr(megalinter, "docker", lambda: "/bin/docker")
    monkeypatch.setattr("shutil.which", lambda name: "/bin/x" if name == "docker" else None)
    target = resolve_target(str(repo), project="app", version="1.0")
    outcome = run_scan(
        target,
        out_dir=tmp_path / "out",
        enabled=store.enabled(),
        record=lambda tool, result: store.record(
            project="app", version="1.0", tool=tool, result=result
        ),
    )
    assert set(outcome.results) == {
        "megalinter:PYTHON_RUFF",
        "megalinter:REPOSITORY_GITLEAKS",
        "megalinter:YAML_YAMLLINT",
    }
    assert "megalinter:REPOSITORY_GRYPE" in outcome.errors
    # Bandit, gitleaks, Ruff ... are MegaLinter's job now, so they are neither run nor missing.
    assert "bandit" not in outcome.skipped and "bandit" not in outcome.expected
    assert "megalinter:REPOSITORY_GRYPE" in outcome.expected
    assert outcome.profile is not None and outcome.profile.source_lines == 2000

    card = build_scorecard("app", "1.0", store=store)
    assert card is not None and card.source_lines == 2000
    scores = {c.key: c.score for c in card.categories}
    assert scores["security"] == 59  # one critical leak
    # ruff 1 low + 1 info, yamllint 4 low + 1 info = 2.7 points in 2,000 lines, 1.35 per 1,000.
    assert scores["code"] == 93
    assert "megalinter:REPOSITORY_SYFT" not in {t.tool for t in card.tools}
    names = {t.tool: t.name for t in card.tools}
    assert names["megalinter:PYTHON_RUFF"] == "ruff (Python)"
    assert "gitleaks (repository)" in to_markdown(card)

    # Switching a linter off leaves it out of the score; an unknown engine linter name is refused.
    store.set_enabled("megalinter:REPOSITORY_GITLEAKS", False)
    card = build_scorecard("app", "1.0", store=store)
    assert card is not None and "security" not in {c.key for c in card.categories if c.score}
    with pytest.raises(ScorecardError):
        store.set_enabled("megalinter:lower_case", False)


def test_without_docker_the_installed_scanners_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("import os\n", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: None)
    outcome = run_scan(
        resolve_target(str(repo), project="p", version="1"),
        out_dir=tmp_path / "out",
        enabled={},
        record=lambda tool, result: None,
    )
    assert outcome.skipped["megalinter"].startswith("not installed")
    assert outcome.skipped["bandit"].startswith("not installed")


# --- new findings since the version before ---------------------------------------


def _ruff(*findings: Finding) -> ToolResult:
    from qasight.services.scorecard.parsers import from_findings

    return from_findings(list(findings), what="issue", clean="No issues")


def test_new_findings_and_the_no_new_gate(store: ScorecardStore) -> None:
    old = Finding("high", "S602", "shell", "app/run.py:10")
    store.record(
        project="p",
        version="1.0",
        tool="megalinter:PYTHON_BANDIT",
        result=_ruff(old),
        name="bandit (Python)",
        category="security",
    )
    moved = Finding("high", "S602", "shell", "app/run.py:42")  # same finding, the line moved
    fresh = Finding("high", "S105", "password", "app/auth.py:3")
    store.record(
        project="p",
        version="1.1",
        tool="megalinter:PYTHON_BANDIT",
        result=_ruff(moved, fresh),
        name="bandit (Python)",
        category="security",
    )
    card = build_scorecard("p", "1.1", store=store)
    assert card is not None
    bandit = card.tools[0]
    assert bandit.new_counts == {"high": 1}
    assert gate_failures(card, no_new=True) == [
        "bandit (Python) has new findings since 1.0: 1 high"
    ]
    assert gate_failures(card) == []
