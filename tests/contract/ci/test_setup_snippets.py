"""The Setup Snippets page and the CI files `qasight init` writes come from the same templates."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

from qasight.services import ci_templates, project_setup


def _script():  # noqa: ANN202
    path = Path("scripts/export_setup_snippets.py").resolve()
    spec = importlib.util.spec_from_file_location("export_setup_snippets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_snippets_page_is_up_to_date() -> None:
    module = _script()
    assert module.PAGE_PATH.read_text(encoding="utf-8") == module.render_page(), (
        "run: python scripts/export_setup_snippets.py"
    )


def _two_stack_project(root: Path) -> project_setup.ProjectFacts:
    (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"devDependencies": {"jest": "1"}}))
    return project_setup.detect_project(root)


def test_every_ci_file_parses_for_a_two_stack_project(tmp_path: Path) -> None:
    facts = _two_stack_project(tmp_path)

    gitlab = yaml.safe_load(ci_templates.gitlab_ci(facts, cycle="shop"))
    assert set(gitlab) == {"stages", "pytest", "jest"}
    # Two jobs, two histories: each stack records its own cycle.
    assert "qasight ingest --cycle shop-jest" in gitlab["jest"]["after_script"]
    assert gitlab["jest"]["image"] == "node:22"

    circle = yaml.safe_load(ci_templates.circleci_config(facts, cycle="shop"))
    assert circle["workflows"]["tests"]["jobs"] == ["pytest", "jest"]
    record = circle["jobs"]["pytest"]["steps"][-3]["run"]
    assert record["when"] == "always" and "qasight ingest --cycle shop-pytest" in record["command"]

    github = yaml.safe_load(ci_templates.zero_config_github_workflow(facts, cycle="shop"))
    steps = github["jobs"]["tests"]["steps"]
    assert steps[-1]["uses"] == ci_templates.ACTION_REF
    assert steps[-1]["if"] == "${{ !cancelled() }}"

    jenkins = ci_templates.jenkinsfile(facts, cycle="shop")
    assert "stage('pytest')" in jenkins and "stage('Jest')" in jenkins
    assert "qasight ingest --cycle shop\n" in jenkins  # one build, one run


def test_test_commands_write_files_qasight_reads(tmp_path: Path) -> None:
    commands = {
        key: ci_templates.ci_test_command(
            project_setup.detect_project(tmp_path, only=[key]).stacks[0]
        )
        for key in project_setup.STACK_KEYS
    }
    assert commands["jest"].endswith("--json --outputFile=test-results/jest.json")
    assert commands["go"] == (
        "mkdir -p test-results && go test -json ./... > test-results/go-test.json"
    )
    assert commands["maven"] == "mvn -B test"
    assert commands["dotnet"] == "dotnet test --logger trx --results-directory test-results/dotnet"
