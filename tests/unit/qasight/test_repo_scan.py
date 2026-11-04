"""``qasight init --scan`` (repo_scan.py) and ``--check`` (stage_check.py)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.loader import discover_and_load
from qasight.services import ci_templates, repo_scan, stage_check
from qasight.services.project_setup import Stack


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _monorepo(root: Path) -> Path:
    _write(root, "api/pyproject.toml", '[project]\nname="api"\n[tool.pytest.ini_options]\n')
    _write(root, "api/tests/test_a.py", "def test_a():\n    pass\n\ndef test_b():\n    pass\n")
    _write(root, "libs/core/pyproject.toml", '[project]\nname="core"\n')
    _write(root, "libs/core/tests/test_c.py", "def test_c():\n    pass\n")
    _write(root, "libs/empty/pyproject.toml", '[project]\nname="empty"\n')
    _write(root, "libs/empty/tests/conftest.py", "")
    _write(root, "libs/empty/tests/test_none.py", "# no tests yet\n")
    _write(root, "web/package.json", json.dumps({"devDependencies": {"jest": "29"}}))
    _write(root, "svc/go.mod", "module x\n\ngo 1.22\n")
    _write(root, "web/node_modules/jest/package.json", "{}")  # never looked at
    return root


def _found(root: Path) -> dict[str, tuple[str, str]]:
    return {
        str(f.stack.stage["name"]): (f.folder, f.stack.key) for f in repo_scan.scan_repo(root).found
    }


def test_scan_finds_a_stage_per_runner_per_folder(tmp_path: Path) -> None:
    found = _found(_monorepo(tmp_path))
    assert found == {
        "api-pytest": ("api", "pytest"),
        "libs-core-pytest": ("libs/core", "pytest"),
        "libs-empty-pytest": ("libs/empty", "pytest"),
        "svc-go": ("svc", "go"),
        "web-jest": ("web", "jest"),
    }


def test_scan_keeps_the_top_folder_names_and_only_filters(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\n[tool.pytest.ini_options]\n')
    _write(tmp_path, "tests/test_a.py", "def test_a(): pass\n")
    _write(tmp_path, "tests/conftest.py", "")  # inside `pytest tests`: not a stage of its own
    _write(tmp_path, "tools/lint/go.mod", "module lint\n")
    result = repo_scan.scan_repo(tmp_path)
    assert [(f.folder, f.stack.stage["name"]) for f in result.found] == [
        (".", "pytest"),
        ("tools/lint", "tools-lint-go"),
    ]
    assert "target_repo" not in result.found[0].stack.stage
    assert [f.stack.key for f in repo_scan.scan_repo(tmp_path, only=["go"]).found] == ["go"]


def test_an_outer_runner_that_already_covers_a_folder_wins(tmp_path: Path) -> None:
    _write(tmp_path, "pom.xml", "<project><modules><module>a</module></modules></project>")
    _write(tmp_path, "a/pom.xml", "<project/>")
    _write(tmp_path, "package.json", json.dumps({"devDependencies": {"jest": "29"}}))
    _write(tmp_path, "packages/ui/package.json", json.dumps({"devDependencies": {"jest": "29"}}))
    _write(tmp_path, "py/pytest.ini", "[pytest]\n")  # bare `pytest` collects below it
    _write(tmp_path, "py/sub/conftest.py", "")

    result = repo_scan.scan_repo(tmp_path)

    assert [(f.folder, f.stack.key) for f in result.found] == [
        (".", "jest"),
        (".", "maven"),
        ("py", "pytest"),
    ]
    assert any("a: Maven (Surefire) left out" in n for n in result.notes)
    assert any("packages/ui: Jest left out" in n for n in result.notes)


def test_dotnet_starts_at_the_solution_else_each_test_project(tmp_path: Path) -> None:
    test_sdk = '<PackageReference Include="xunit" />'
    _write(tmp_path, "shop/Shop.sln")
    _write(tmp_path, "shop/Shop.Tests/Shop.Tests.csproj", test_sdk)
    _write(tmp_path, "tools/A.Tests/A.Tests.csproj", test_sdk)
    _write(tmp_path, "tools/B.Tests/B.Tests.csproj", test_sdk)
    folders = sorted(f.folder for f in repo_scan.scan_repo(tmp_path).found)
    assert folders == ["shop", "tools/A.Tests", "tools/B.Tests"]


def test_skipped_folders_are_not_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "node_modules/x/package.json", json.dumps({"devDependencies": {"jest": "1"}}))
    _write(tmp_path, ".venv/lib/pytest.ini", "[pytest]\n")
    assert repo_scan.scan_repo(tmp_path).found == ()


def test_scanned_config_loads_and_ci_commands_run_in_each_folder(tmp_path: Path) -> None:
    result = repo_scan.scan_repo(_monorepo(tmp_path))
    from qasight.services.project_setup import config_yaml

    (tmp_path / "qasight.yaml").write_text(config_yaml(result.facts, cycle="mono"))
    cfg = discover_and_load(config_path=tmp_path / "qasight.yaml")
    stages = {s.name: s for s in cfg.cycles["mono"].stages}
    assert stages["api-pytest"].target_repo == (tmp_path / "api").resolve()
    assert stages["svc-go"].target_repo == (tmp_path / "svc").resolve()

    by_name = {str(s.stage["name"]): s for s in result.facts.stacks}
    assert ci_templates.ci_test_command(by_name["svc-go"]).startswith("(cd svc && mkdir -p")
    assert by_name["api-pytest"].install[0] == "(cd api && pip install -e .)"
    assert "pip install pytest" in by_name["api-pytest"].install  # same anywhere: no cd
    assert result.facts.go_version_file == "svc/go.mod"
    gitlab = yaml.safe_load(ci_templates.gitlab_ci(result.facts, cycle="mono"))
    assert any(
        "(cd web && " in line
        for job in gitlab.values()
        if isinstance(job, dict)
        for line in job.get("script", [])
    )


# --- --check ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "output", "expected"),
    [
        ("pytest", "a.py::test_a\na.py::test_b\n\n2 tests collected in 0.01s\n", (2, "tests")),
        ("pytest", "\nno tests collected in 0.01s\n", (0, "tests")),
        ("jest", "/r/web/a.test.js\n/r/web/b.test.tsx\n", (2, "test files")),
        ("playwright", "Listing tests:\n  [chromium] a\nTotal: 7 tests in 2 files\n", (7, "tests")),
        ("go", "TestA\nTestB\nExampleC\nok  \tx\t0.01s\n", (3, "tests")),
        (
            "dotnet",
            "Build ok\nThe following Tests are available:\n    A.T1\n    A.T2\n",
            (2, "tests"),
        ),
        ("rspec", 'noise {"summary": {"example_count": 4}}', (4, "tests")),
        ("phpunit", "Available tests:\n - A::t1\n - A::t2\n", (2, "tests")),
    ],
)
def test_count_listed_reads_each_runners_listing(
    key: str, output: str, expected: tuple[int, str]
) -> None:
    assert stage_check.count_listed(key, output) == expected


def _pytest_stack(folder: str) -> Stack:
    return Stack(
        key="pytest",
        label="pytest",
        language="python",
        stage={"name": "p", "equipment": "pytest", "args": ["-q", "tests"], "target_repo": folder},
    )


def test_check_stage_lists_pytest_tests_without_running_them(tmp_path: Path) -> None:
    _monorepo(tmp_path)
    marker = tmp_path / "ran"
    _write(tmp_path, "api/tests/test_side.py", f"def test_s():\n    open({str(marker)!r}, 'w')\n")

    result = stage_check.check_stage(_pytest_stack("api"), root=tmp_path)

    assert (result.status, result.count, result.summary) == ("ok", 3, "3 tests")
    assert not marker.exists()  # collected, never run
    empty = stage_check.check_stage(_pytest_stack("libs/empty"), root=tmp_path)
    assert (empty.status, empty.summary) == ("empty", "no tests found")


def test_check_stage_reports_missing_runners_and_ones_it_cannot_list(tmp_path: Path) -> None:
    jest = Stack(
        key="jest", label="Jest", language="node", stage={"name": "j", "equipment": "jest"}
    )
    missing = stage_check.check_stage(jest, root=tmp_path)
    assert missing.status == "missing"
    assert "jest isn't installed" in missing.summary
    maven = Stack(key="maven", label="Maven", language="java", stage={"name": "m"})
    assert stage_check.check_stage(maven, root=tmp_path).status == "skipped"


@pytest.mark.skipif(os.name == "nt", reason="uses a shell script as the fake runner")
def test_node_runners_come_from_the_projects_node_modules(tmp_path: Path) -> None:
    fake = _write(tmp_path, "node_modules/.bin/jest", "#!/bin/sh\necho /r/a.test.js\n")
    fake.chmod(0o755)
    _write(tmp_path, "web/package.json", "{}")
    jest = Stack(
        key="jest",
        label="Jest",
        language="node",
        stage={"name": "j", "equipment": "jest", "target_repo": "web"},
    )
    result = stage_check.check_stage(jest, root=tmp_path)
    assert (result.status, result.summary, result.command) == (
        "ok",
        "1 test file",
        "jest --listTests",
    )


# --- the command -----------------------------------------------------------------------------


def test_init_scan_asks_before_writing_and_no_answer_writes_nothing(tmp_path: Path) -> None:
    _monorepo(tmp_path)
    result = CliRunner().invoke(app, ["init", "--dir", str(tmp_path), "--scan", "--no-run"])
    assert result.exit_code == 2, result.output
    assert "Found 5 test suite(s)" in result.output
    assert "api-pytest  pytest in api/" in result.output
    assert "Nothing written" in result.output
    assert not (tmp_path / "qasight.yaml").exists()


def test_init_scan_check_drops_empty_stages_then_writes_on_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _monorepo(tmp_path)
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent))  # no go here

    result = CliRunner().invoke(
        app,
        ["init", "--dir", str(tmp_path), "--scan", "--check", "--no-run", "--ci", "none"],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert "api-pytest: 2 tests" in result.output
    assert "libs-empty-pytest: no tests found" in result.output
    assert "svc-go: not checked: go isn't installed" in result.output
    cfg = discover_and_load(config_path=tmp_path / "qasight.yaml")
    names = [s.name for s in cfg.cycles[tmp_path.name.lower()].stages]
    assert names == ["api-pytest", "libs-core-pytest", "svc-go", "web-jest"]
    assert "found no tests" in (tmp_path / "qasight.yaml").read_text()


def test_init_scan_dry_run_prints_and_writes_nothing(tmp_path: Path) -> None:
    _monorepo(tmp_path)
    result = CliRunner().invoke(
        app, ["init", "--dir", str(tmp_path), "--scan", "--dry-run", "--cycle", "mono"]
    )
    assert result.exit_code == 0, result.output
    assert 'target_repo: "libs/core"' in result.output
    assert "Go ahead?" not in result.output
    assert not (tmp_path / "qasight.yaml").exists()


def test_init_without_scan_points_at_it(tmp_path: Path) -> None:
    _monorepo(tmp_path)
    result = CliRunner().invoke(app, ["init", "--dir", str(tmp_path), "--no-run"])
    assert result.exit_code == 2
    assert "--scan" in result.output
