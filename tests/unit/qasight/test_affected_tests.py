"""Affected-test selection: ``select_tests``, ``qasight affected`` and ``pytest --qasight-affected``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.affected import AffectedConfig, AffectedRule, parse_affected
from qasight.config.errors import ConfigValidationError
from qasight.config.loader import load_config
from qasight.services.affected_tests import (
    AffectedError,
    compute,
    load_coverage_map,
    matches,
    select_tests,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

_FILES = ["src/pay/charge.py", "src/pay/refund.py", "tests/pay/test_charge.py", "tests/test_x.py"]


def _rules(*pairs: tuple[str, str]) -> AffectedConfig:
    return AffectedConfig(rules=tuple(AffectedRule((c,), (t,)) for c, t in pairs))


def test_globs_are_gitignore_style() -> None:
    assert matches("docs/a/b.md", ["docs/**"])
    assert matches("README.md", ["*.md"]) and matches("docs/x.md", ["*.md"])
    assert matches("a/conftest.py", ["conftest.py"])
    assert not matches("src/docs/a.md", ["docs/**"])
    assert matches("src/pay/charge.py", ["src/**/charge.py"])
    assert matches("src/charge.py", ["src/**/charge.py"])
    assert not matches("src/pay/charge.py", ["src/*.py"])
    assert matches("build/x/y", ["build/"])


def test_rule_picks_its_tests_and_says_why(tmp_path: Path) -> None:
    config = _rules(("src/pay/**", "tests/pay/**"))
    sel = select_tests(["src/pay/charge.py"], config, root=tmp_path, all_files=_FILES)
    assert not sel.run_all
    assert list(sel.tests) == ["tests/pay/test_charge.py"]
    assert sel.tests["tests/pay/test_charge.py"] == [
        "src/pay/charge.py: matched a rule in affected.rules"
    ]


def test_unmapped_file_runs_everything(tmp_path: Path) -> None:
    config = _rules(("src/pay/**", "tests/pay/**"))
    sel = select_tests(
        ["src/pay/charge.py", "src/other.py"], config, root=tmp_path, all_files=_FILES
    )
    assert sel.run_all
    assert sel.run_all_reasons == ["src/other.py changed and no rule or test-file pattern maps it"]


def test_rule_whose_tests_are_gone_runs_everything(tmp_path: Path) -> None:
    config = _rules(("src/pay/**", "tests/billing/**"))
    sel = select_tests(["src/pay/charge.py"], config, root=tmp_path, all_files=_FILES)
    assert sel.run_all
    assert "tests/billing/**" in sel.run_all_reasons[0]


def test_run_all_when_ignore_and_empty_diff(tmp_path: Path) -> None:
    config = parse_affected({"ignore": ["docs/**"]}, config_dir=tmp_path)
    assert select_tests(["sub/conftest.py"], config, root=tmp_path, all_files=[]).run_all
    assert select_tests(["poetry.lock"], config, root=tmp_path, all_files=[]).run_all is True
    docs = select_tests(["docs/a.md", "x/__pycache__/m.pyc"], config, root=tmp_path, all_files=[])
    assert docs.nothing_to_run and not docs.run_all
    empty = select_tests([], config, root=tmp_path, all_files=[])
    assert empty.run_all and "no changed files" in empty.run_all_reasons[0]


def test_changed_test_file_runs_and_deleted_one_does_not(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    sel = select_tests(
        ["tests/test_a.py", "tests/test_gone.py", "web/button.spec.ts"],
        AffectedConfig(),
        root=tmp_path,
        all_files=[],
    )
    assert list(sel.tests) == ["tests/test_a.py"]
    outcomes = {d.path: d.outcome for d in sel.files}
    assert outcomes["tests/test_gone.py"] == "deleted_test"
    assert outcomes["web/button.spec.ts"] == "deleted_test"


def _coverage(tmp_path: Path, files: dict[str, dict[str, list[str]] | None]) -> Path:
    report = {
        "files": {
            name: ({"contexts": ctx} if ctx is not None else {}) for name, ctx in files.items()
        }
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_coverage_maps_files_to_the_tests_that_ran_them(tmp_path: Path) -> None:
    path = _coverage(
        tmp_path,
        {
            "src/pay/charge.py": {
                "3": ["tests/pay/test_charge.py::test_ok|run", ""],
                "4": [
                    "tests/pay/test_charge.py::test_ok|run",
                    "tests/test_x.py::TestA::test_b|run",
                ],
            },
            str(tmp_path / "src/pay/refund.py"): {"1": [""]},
        },
    )
    coverage = load_coverage_map(path, tmp_path)
    assert coverage["src/pay/refund.py"] == frozenset()
    sel = select_tests(
        ["src/pay/charge.py"], AffectedConfig(), root=tmp_path, coverage=coverage, all_files=[]
    )
    assert list(sel.tests) == [
        "tests/pay/test_charge.py::test_ok",
        "tests/test_x.py::TestA::test_b",
    ]
    only_import = select_tests(
        ["src/pay/refund.py"], AffectedConfig(), root=tmp_path, coverage=coverage, all_files=[]
    )
    assert only_import.run_all
    new_file = select_tests(
        ["src/pay/new.py"], AffectedConfig(), root=tmp_path, coverage=coverage, all_files=[]
    )
    assert new_file.run_all and "coverage report doesn't list it" in new_file.run_all_reasons[0]


def test_coverage_without_contexts_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(AffectedError, match="cov-context=test"):
        load_coverage_map(_coverage(tmp_path, {"src/a.py": None}), tmp_path)
    (tmp_path / "bad.json").write_text("[]", encoding="utf-8")
    with pytest.raises(AffectedError):
        load_coverage_map(tmp_path / "bad.json", tmp_path)


def test_a_whole_file_absorbs_its_node_ids(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("", encoding="utf-8")
    config = _rules(("src/a.py", "tests/test_x.py::test_one"))
    sel = select_tests(["src/a.py", "tests/test_x.py"], config, root=tmp_path, all_files=[])
    assert list(sel.tests) == ["tests/test_x.py"]
    assert len(sel.tests["tests/test_x.py"]) == 2


def test_config_section_is_validated(tmp_path: Path) -> None:
    for bad in (
        [],
        {"nope": 1},
        {"rules": {"a": 1}},
        {"rules": [{"changed": "a"}]},
        {"rules": [{"changed": "a", "tests": "b", "x": 1}]},
        {"ignore": [1]},
    ):
        with pytest.raises(ConfigValidationError):
            parse_affected(bad, config_dir=tmp_path)
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(
        "affected:\n  base: develop\n  coverage: cov.json\n"
        "  rules:\n    - changed: src/**\n      tests: [tests/**]\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg).affected
    assert loaded.base == "develop"
    assert loaded.coverage == tmp_path / "cov.json"
    assert loaded.rules == (AffectedRule(("src/**",), ("tests/**",)),)


# -- git, CLI and pytest -----------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "docs").mkdir()
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "src" / "other.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(
        "def test_add(): pass\ndef test_sub(): pass\n", encoding="utf-8"
    )
    (repo / "tests" / "test_other.py").write_text("def test_other(): pass\n", encoding="utf-8")
    (repo / "docs" / "a.md").write_text("hi\n", encoding="utf-8")
    (repo / "qasight.yaml").write_text(
        "affected:\n  base: main\n  ignore: docs/**\n"
        "  rules:\n    - changed: src/calc.py\n      tests: tests/test_calc.py\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "feature")
    return repo


def test_compute_diffs_against_the_merge_base(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
    (repo / "docs" / "new.md").write_text("new\n", encoding="utf-8")  # untracked counts too
    config = load_config(repo / "qasight.yaml").affected
    sel, root = compute(repo, config)
    assert root == repo.resolve()
    assert sel.base == "main"
    assert set(sel.changed) == {"src/calc.py", "docs/new.md"}
    assert list(sel.tests) == ["tests/test_calc.py"]


def test_compute_runs_all_when_the_base_is_unknown(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    sel, _ = compute(repo, AffectedConfig(), base="no-such-branch")
    assert sel.run_all and "could not diff against no-such-branch" in sel.run_all_reasons[0]


def test_compute_outside_git_runs_all(tmp_path: Path) -> None:
    sel, _ = compute(tmp_path, AffectedConfig())
    assert sel.run_all and "not in a git repository" in sel.run_all_reasons[0]


def test_cli_text_list_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    (repo / "src" / "calc.py").write_text("x = 1\n", encoding="utf-8")
    runner = CliRunner()
    text = runner.invoke(app, ["affected"])
    assert text.exit_code == 0, text.output
    assert "tests/test_calc.py" in text.output and "because src/calc.py" in text.output
    listed = runner.invoke(app, ["affected", "--list"])
    assert listed.output.split() == ["tests/test_calc.py"]
    (repo / "src" / "other.py").write_text("X = 2\n", encoding="utf-8")
    payload = json.loads(runner.invoke(app, ["affected", "--json"]).output)
    assert payload["run_all"] is True
    assert payload["run_all_reasons"] == [
        "src/other.py changed and no rule or test-file pattern maps it"
    ]
    assert runner.invoke(app, ["affected", "--list"]).output == ""


def test_cli_reads_changed_files_and_rejects_bad_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    runner = CliRunner()
    done = runner.invoke(app, ["affected", "--changed", "-", "--list"], input="./docs/a.md\n")
    assert done.exit_code == 0 and done.output == ""
    shown = runner.invoke(app, ["affected", "--changed", "-"], input="docs/a.md\n")
    assert "No test needs to run" in shown.output
    assert runner.invoke(app, ["affected", "--list", "--json"]).exit_code == 2
    assert runner.invoke(app, ["affected", "--coverage", "missing.json"]).exit_code == 2


def _pytest(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])}
    env.pop("TESTO_AFFECTED", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:xdist"]
        + ["--no-cov", "-rA", "--qasight-affected", *args, "tests"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_pytest_runs_only_affected_tests(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src" / "calc.py").write_text("x = 1\n", encoding="utf-8")
    done = _pytest(repo)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "2 passed, 1 deselected" in done.stdout
    assert "qasight: affected tests: 1 picked from 1 changed file(s) against main" in done.stdout
    assert "tests/test_calc.py  <- src/calc.py: matched a rule" in done.stdout


def test_pytest_runs_everything_when_unsure(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src" / "other.py").write_text("X = 2\n", encoding="utf-8")
    done = _pytest(repo)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "3 passed" in done.stdout and "deselected" not in done.stdout
    assert "running everything, because" in done.stdout


def test_pytest_with_nothing_to_run_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "docs" / "a.md").write_text("changed\n", encoding="utf-8")
    done = _pytest(repo)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "3 deselected" in done.stdout
    assert "no test needs to run" in done.stdout


def test_pytest_runs_all_when_a_picked_test_is_not_collected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "qasight.yaml").write_text(
        "affected:\n  base: main\n  rules:\n    - changed: src/calc.py\n"
        "      tests: tests/test_calc.py::test_renamed\n",
        encoding="utf-8",
    )
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qam", "rule")
    (repo / "src" / "calc.py").write_text("x = 1\n", encoding="utf-8")
    done = _pytest(repo, "--qasight-affected-base", "HEAD")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "3 passed" in done.stdout
    assert "picked but not collected (test_calc.py::test_renamed)" in done.stdout


# --- bug hunt 2026-10-31 ---------------------------------------------------------------


def test_a_folder_glob_picks_only_test_files(tmp_path: Path) -> None:
    files = [*_FILES, "tests/pay/__init__.py", "tests/pay/helpers.py"]
    config = _rules(("src/pay/**", "tests/pay/**"))
    sel = select_tests(["src/pay/charge.py"], config, root=tmp_path, all_files=files)
    # __init__.py and helpers.py are never collected; picking them made pytest run everything.
    assert list(sel.tests) == ["tests/pay/test_charge.py"]


def test_a_folder_glob_with_no_test_file_names_keeps_its_matches(tmp_path: Path) -> None:
    config = _rules(("src/pay/**", "e2e/**"))
    sel = select_tests(
        ["src/pay/charge.py"], config, root=tmp_path, all_files=[*_FILES, "e2e/checkout.ts"]
    )
    assert list(sel.tests) == ["e2e/checkout.ts"]


def test_non_ascii_test_file_names_are_picked(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "tests" / "test_é.py").write_text("def test_e(): pass\n", encoding="utf-8")
    (repo / "qasight.yaml").write_text(
        "affected:\n  base: main\n  rules:\n    - changed: src/**\n      tests: tests/**\n",
        encoding="utf-8",
    )
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "e")
    (repo / "src" / "calc.py").write_text("X = 2\n", encoding="utf-8")
    sel, _ = compute(repo, load_config(repo / "qasight.yaml").affected)
    assert "tests/test_é.py" in sel.tests
