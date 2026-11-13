"""``qasight init --attach``: QASight added to the workflows that already run the tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.services import ci_attach

_CI = """\
name: CI
on: [push, pull_request]
permissions:
  contents: read
jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e . pytest
      - name: Tests
        run: pytest -q tests  # unit tests

      # docs afterwards
      - run: echo done
  web:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - run: npm ci
      - name: Jest
        run: |
          echo starting
          npm test
  go:
    runs-on: ubuntu-latest
    steps:
      - run: go test ./...
"""


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _repo(root: Path, workflow: str = _CI) -> Path:
    _write(root, ".github/workflows/ci.yml", workflow)
    _write(root, "web/package.json", json.dumps({"scripts": {"test": "jest --ci"}}))
    _write(root, "pyproject.toml", '[project]\nname="shop"\n[tool.pytest.ini_options]\n')
    _write(root, "tests/test_a.py", "def test_a():\n    pass\n")
    return root


def _steps(text: str, job: str) -> list[dict[str, object]]:
    return yaml.safe_load(text)["jobs"][job]["steps"]


def test_attach_adds_result_flags_and_one_step_per_job(tmp_path: Path) -> None:
    plan = ci_attach.plan_attach(_repo(tmp_path), cycle="shop")
    text = plan.texts[tmp_path / ".github/workflows/ci.yml"]

    python = _steps(text, "python")
    assert python[1] == {"run": "pip install -e . pytest"}  # a package name, not a test run
    assert python[2]["run"] == "pytest -q tests --junitxml=qasight-pytest.xml"
    assert "--junitxml=qasight-pytest.xml  # unit tests" in text  # the comment stays
    assert python[3]["uses"] == ci_attach.ACTION_REF
    assert python[3]["if"] == "${{ !cancelled() }}"
    assert python[3]["with"] == {
        "results": "qasight-pytest.xml",
        "cycle": "shop-python",
        "comment-on-pr": "true",
    }
    assert python[4] == {"run": "echo done"}  # after the last test step, not at the end

    web = _steps(text, "web")
    assert web[1]["run"].endswith("npm test -- --json --outputFile=qasight-jest.json\n")
    assert web[2]["with"]["results"] == "web/qasight-jest.json"  # the job's working directory
    assert _steps(text, "go") == [{"run": "go test ./..."}]
    assert any("go test: add `-json`" in n for n in plan.notes)
    assert yaml.safe_load(text)["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert [(s.job, s.runner, s.edited) for s in plan.steps] == [
        ("python", "pytest", True),
        ("web", "Jest", True),
    ]
    assert '+          cycle: "shop-web"' in plan.diff(tmp_path)


def test_one_job_gets_the_plain_cycle_name_and_maven_needs_no_flag(tmp_path: Path) -> None:
    _write(
        tmp_path,
        ".github/workflows/java.yaml",
        "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: ./mvnw -B verify\n",
    )
    plan = ci_attach.plan_attach(tmp_path, cycle="app")
    (text,) = plan.texts.values()
    steps = _steps(text, "build")
    assert steps[0] == {"run": "./mvnw -B verify"}
    assert steps[1]["with"]["results"] == "**/target/*-reports/TEST-*.xml"
    assert steps[1]["with"]["cycle"] == "app"
    assert "permissions" not in yaml.safe_load(text)  # none to extend


def test_commands_it_cannot_extend_safely_are_listed_not_edited(tmp_path: Path) -> None:
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "on: push\njobs:\n"
        "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest -q && echo ok\n"
        "  b:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest --junitxml=out.xml\n"
        "  c:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: arieleven/qasight@v1\n"
        "      - run: pytest\n",
    )
    plan = ci_attach.plan_attach(tmp_path, cycle="x")
    assert plan.texts == {}
    joined = "\n".join(plan.notes)
    assert "isn't the last command on its line" in joined
    assert "already writes a result file" in joined
    assert "'c' already uses QASight" in joined


def test_quoted_commands_pnpm_scripts_and_restricted_permissions(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", json.dumps({"scripts": {"test:unit": "vitest run"}}))
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    permissions: read-all\n"
        "    steps:\n"
        '      - run: "CI=1 pytest -q"\n'
        "      - run: pnpm test:unit\n",
    )
    plan = ci_attach.plan_attach(tmp_path, cycle="x")
    (text,) = plan.texts.values()
    steps = _steps(text, "t")
    assert steps[0]["run"] == "CI=1 pytest -q --junitxml=qasight-pytest.xml"
    assert steps[1]["run"] == (
        "pnpm test:unit --reporter=default --reporter=json --outputFile.json=qasight-vitest.json"
    )
    assert steps[2]["with"]["results"] == "qasight-pytest.xml qasight-vitest.json"
    assert any("pull-requests: write" in n for n in plan.notes)


def test_init_attach_dry_run_shows_the_diff_and_writes_nothing(tmp_path: Path) -> None:
    _repo(tmp_path)
    before = (tmp_path / ".github/workflows/ci.yml").read_text()
    result = CliRunner().invoke(
        app, ["init", "--dir", str(tmp_path), "--attach", "--dry-run", "--ci", "github"]
    )
    assert result.exit_code == 0, result.output
    assert "your workflow, changed like this" in result.output
    assert "+      - name: QASight" in result.output
    assert (tmp_path / ".github/workflows/ci.yml").read_text() == before
    assert not (tmp_path / ".github/workflows/qasight.yml").exists()


def test_init_attach_edits_the_workflow_instead_of_writing_a_second(tmp_path: Path) -> None:
    _repo(tmp_path)
    result = CliRunner().invoke(
        app, ["init", "--dir", str(tmp_path), "--attach", "--no-run", "--ci", "github"]
    )
    assert result.exit_code == 0, result.output
    assert "Updated .github/workflows/ci.yml" in result.output
    assert not (tmp_path / ".github/workflows/qasight.yml").exists()
    assert "qasight-pytest.xml" in (tmp_path / ".github/workflows/ci.yml").read_text()


def test_init_suggests_attach_and_falls_back_without_test_steps(tmp_path: Path) -> None:
    _repo(tmp_path)
    hint = CliRunner().invoke(app, ["init", "--dir", str(tmp_path), "--dry-run", "--ci", "github"])
    assert "qasight init --attach" in hint.output

    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "on: push\njobs:\n  a:\n    steps:\n      - run: make\n",
    )
    fallback = CliRunner().invoke(
        app, ["init", "--dir", str(tmp_path), "--attach", "--no-run", "--ci", "github"]
    )
    assert fallback.exit_code == 0, fallback.output
    assert "a workflow of its own is written instead" in fallback.output
    assert (tmp_path / ".github/workflows/qasight.yml").is_file()

    other = CliRunner().invoke(app, ["init", "--dir", str(tmp_path), "--attach", "--ci", "gitlab"])
    assert other.exit_code == 2


def test_setup_workflow_is_valid_and_matches_getting_started() -> None:
    from qasight.services.ci_templates import SETUP_WORKFLOW_PATH, setup_workflow

    text = setup_workflow()
    data = yaml.safe_load(text)
    assert data["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert data[True]["push"]["paths"] == [SETUP_WORKFLOW_PATH]  # YAML 1.1 reads `on` as true
    assert data["jobs"]["setup"]["steps"][-1]["with"] == {"setup": "true"}
    guide = (Path(__file__).resolve().parents[3] / "docs/Getting Started.md").read_text()
    assert f"```yaml\n{text}```" in guide  # the page's button sends this exact text


def test_compare_url_from_actions_env_or_a_github_origin(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    import subprocess

    from qasight.services.setup_pr import compare_url

    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/shop")
    assert compare_url(tmp_path, base="main", branch="qasight-setup") == (
        "https://github.com/acme/shop/compare/main...qasight-setup?expand=1"
    )
    monkeypatch.delenv("GITHUB_SERVER_URL")
    monkeypatch.delenv("GITHUB_REPOSITORY")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:acme/shop.git"],
        check=True,
    )
    assert compare_url(tmp_path, base="dev", branch="b") == (
        "https://github.com/acme/shop/compare/dev...b?expand=1"
    )
