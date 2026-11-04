"""``qasight init``'s detection (qasight/services/project_setup.py), CI template and command."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.loader import discover_and_load
from qasight.services import ci_templates, project_setup

_JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="s" tests="2" failures="1">
  <testcase classname="c" name="ok" time="0.1"/>
  <testcase classname="c" name="bad" time="0.2"><failure message="boom">boom</failure></testcase>
</testsuite></testsuites>
"""


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _keys(root: Path) -> list[str]:
    return [s.key for s in project_setup.detect_stacks(root)]


def test_detects_each_stack_from_its_project_files(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\n[tool.pytest.ini_options]\n')
    _write(
        tmp_path,
        "package.json",
        json.dumps({"devDependencies": {"jest": "^29", "@playwright/test": "^1", "cypress": "1"}}),
    )
    _write(tmp_path, "go.mod", "module example.com/x\n")
    _write(tmp_path, "src/App.Tests/App.Tests.csproj", '<PackageReference Include="xunit" />')
    _write(tmp_path, "pom.xml", "<project/>")
    _write(tmp_path, "Gemfile", "gem 'rspec'\n")
    _write(tmp_path, "composer.json", '{"require-dev": {"phpunit/phpunit": "^11"}}')

    facts = project_setup.detect_project(tmp_path)

    assert [s.key for s in facts.stacks] == [
        "pytest",
        "jest",
        "playwright",
        "go",
        "dotnet",
        "maven",
        "rspec",
        "phpunit",
    ]
    assert any("Cypress" in note for note in facts.skipped)
    assert facts.go_version_file == "go.mod"
    assert facts.java_build == "maven"


def test_an_empty_folder_has_no_stack(tmp_path: Path) -> None:
    assert _keys(tmp_path) == []


def test_a_non_test_csproj_is_not_dotnet_tests(tmp_path: Path) -> None:
    _write(tmp_path, "App/App.csproj", "<Project Sdk='Microsoft.NET.Sdk'/>")
    assert _keys(tmp_path) == []


def test_only_overrides_detection(tmp_path: Path) -> None:
    assert [s.key for s in project_setup.detect_project(tmp_path, only=["vitest"]).stacks] == [
        "vitest"
    ]


def test_node_package_manager_and_install(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", json.dumps({"devDependencies": {"vitest": "^2"}}))
    _write(tmp_path, "pnpm-lock.yaml", "")
    facts = project_setup.detect_project(tmp_path)
    assert facts.node_package_manager == "pnpm"
    assert facts.stacks[0].install == ("pnpm install --frozen-lockfile",)


def test_python_install_uses_requirements_and_skips_pytest_when_listed(tmp_path: Path) -> None:
    _write(tmp_path, "requirements-dev.txt", "pytest==8\n")
    _write(tmp_path, "tests/test_a.py", "def test_a(): pass\n")
    (stack,) = project_setup.detect_stacks(tmp_path)
    assert stack.install == ("pip install -r requirements-dev.txt",)
    assert stack.stage["args"] == ["-q", "tests"]


def test_python_install_uses_a_test_extra(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname="x"\nrequires-python=">=3.13"\n'
        '[project.optional-dependencies]\ntest=["pytest"]\n',
    )
    facts = project_setup.detect_project(tmp_path)
    assert facts.stacks[0].install == ('pip install -e ".[test]"', "pip install pytest")
    assert facts.python_version == "3.13"


def test_config_yaml_loads_and_runs_every_stack(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", json.dumps({"devDependencies": {"jest": "1"}}))
    _write(tmp_path, "go.mod", "module x\n")
    facts = project_setup.detect_project(tmp_path)
    path = _write(tmp_path, "qasight.yaml", project_setup.config_yaml(facts, cycle="web"))

    cfg = discover_and_load(config_path=path)
    stages = cfg.cycles["web"].stages
    assert [s.framework for s in stages] == ["jest", "command"]
    assert stages[1].junit_xml == ("test-results/go-test.json",)


def test_github_workflow_sets_up_each_language_then_runs_the_action(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.txt", "pytest\n")
    _write(tmp_path, "package.json", json.dumps({"devDependencies": {"jest": "1"}}))
    _write(tmp_path, "package-lock.json", "{}")
    _write(tmp_path, ".nvmrc", "22\n")
    facts = project_setup.detect_project(tmp_path)

    text = ci_templates.github_workflow(facts, cycle="shop", branch="trunk")
    doc = yaml.safe_load(text)

    assert doc[True]["push"]["branches"] == ["trunk"]  # YAML 1.1 reads `on` as True
    assert doc["permissions"]["pull-requests"] == "write"
    steps = doc["jobs"]["tests"]["steps"]
    uses = [s.get("uses") or s.get("run") for s in steps]
    assert uses == [
        "actions/checkout@v4",
        "actions/setup-python@v5",
        "pip install -r requirements.txt",
        "actions/setup-node@v4",
        "npm ci",
        ci_templates.ACTION_REF,
    ]
    assert steps[3]["with"] == {"node-version-file": ".nvmrc", "cache": "npm"}
    assert steps[-1]["with"] == {
        "cycle": "shop",
        "comment-on-pr": "true",
        "python-version": "3.12",
    }


def test_find_result_files_skips_git_tracked_fixtures_and_non_results(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _write(tmp_path, "tests/fixtures/sample-junit.xml", _JUNIT)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    _write(tmp_path, "reports/junit.xml", _JUNIT)
    _write(tmp_path, "coverage.xml", "<coverage/>")
    _write(tmp_path, "package.json", '{"name": "x"}')
    _write(tmp_path, "node_modules/x/junit.xml", _JUNIT)
    _write(tmp_path, "test-results/jest.json", json.dumps({"numTotalTests": 1, "testResults": []}))
    _write(tmp_path, "allure-results/a-result.json", '{"uuid": "1", "status": "passed"}')

    found = project_setup.find_result_files(tmp_path)
    rel = sorted(p.relative_to(tmp_path).as_posix() for p in found)
    assert rel == ["allure-results/a-result.json", "reports/junit.xml", "test-results/jest.json"]
    assert sorted(project_setup.result_patterns(found, repo=tmp_path)) == [
        "allure-results",
        "reports/junit.xml",
        "test-results/jest.json",
    ]


def test_missing_gitignore_lines(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "/testo_history.db\nartifacts\n")
    assert project_setup.missing_gitignore_lines(tmp_path) == [".testo/", "test-results/"]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("TESTO_HOME", "CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "My Shop"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{root / 'testo_history.db'}")
    _write(root, "requirements.txt", "pytest\n")
    _write(root, "reports/junit.xml", _JUNIT)
    monkeypatch.chdir(tmp_path)
    return root


def test_init_writes_config_workflow_gitignore_and_records_existing_results(
    project: Path,
) -> None:
    result = CliRunner().invoke(app, ["init", "--dir", str(project)])

    assert result.exit_code == 0, result.output
    assert "Found: pytest" in result.output
    cfg = discover_and_load(config_path=project / "qasight.yaml")
    assert list(cfg.cycles) == ["my-shop"]
    workflow = yaml.safe_load((project / ".github/workflows/qasight.yml").read_text())
    assert workflow["jobs"]["tests"]["steps"][-1]["with"]["cycle"] == "my-shop"
    assert "testo_history.db" in (project / ".gitignore").read_text()
    with sqlite3.connect(project / "testo_history.db") as db:
        rows = db.execute("select cycle from runrecord").fetchall()
    assert rows == [("my-shop",)]


def test_init_keeps_existing_files_and_dry_run_writes_nothing(project: Path) -> None:
    _write(
        project,
        "qasight.yaml",
        "version: 1\ncycles:\n  mine:\n    stages:\n      - {name: u, equipment: pytest}\n",
    )

    dry = CliRunner().invoke(app, ["init", "--dir", str(project), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "would be kept" in dry.output
    assert 'cycle: "mine"' in dry.output  # the kept config's only cycle
    assert not (project / ".github").exists()
    assert not (project / ".gitignore").exists()


def test_init_without_a_runner_says_what_it_looked_for(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["init", "--dir", str(tmp_path), "--no-run"])
    assert result.exit_code == 2
    assert "No test runner found" in result.output


def test_ingest_without_arguments_finds_results_and_names_the_cycle(project: Path) -> None:
    result = CliRunner().invoke(app, ["ingest", "--repo", str(project)])

    assert result.exit_code == 1, result.output  # one test failed in the report
    assert "Found 1 result file(s): reports/junit.xml" in result.output
    with sqlite3.connect(project / "testo_history.db") as db:
        assert db.execute("select cycle from runrecord").fetchall() == [("my-shop",)]


def test_ingest_without_arguments_or_results_says_so(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["ingest", "--repo", str(tmp_path), "--ci"])
    assert result.exit_code == 2
    assert "no result files found" in result.output


def test_init_ci_gitlab_writes_gitlab_ci_or_a_file_next_to_yours(project: Path) -> None:
    first = CliRunner().invoke(app, ["init", "--dir", str(project), "--ci", "gitlab", "--no-run"])
    assert first.exit_code == 0, first.output
    assert "pytest" in yaml.safe_load((project / ".gitlab-ci.yml").read_text())

    (project / "Jenkinsfile").write_text("pipeline {}\n")
    second = CliRunner().invoke(app, ["init", "--dir", str(project), "--ci", "jenkins", "--no-run"])
    assert second.exit_code == 0, second.output
    assert (project / "Jenkinsfile").read_text() == "pipeline {}\n"
    assert "post {" in (project / "Jenkinsfile.qasight").read_text()
    assert "left alone" in second.output


def test_init_ci_auto_follows_the_ci_already_in_use(project: Path) -> None:
    (project / ".circleci").mkdir()
    (project / ".circleci/config.yml").write_text("version: 2.1\n")
    result = CliRunner().invoke(app, ["init", "--dir", str(project), "--no-run"])
    assert result.exit_code == 0, result.output
    assert (project / ".circleci/qasight.yml").is_file()
    assert not (project / ".github").exists()
