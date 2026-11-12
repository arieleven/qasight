"""``qasight run -- <command>``: any test command, recorded without a config."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.services import wrap_run

OUT = Path("/out")


def _prep(argv: list[str], cwd: Path) -> wrap_run.Wrapped:
    return wrap_run.prepare(argv, cwd=cwd, out_dir=OUT)


@pytest.mark.parametrize(
    ("argv", "runner", "added"),
    [
        (["pytest", "-q"], "pytest", [f"--junitxml={OUT / 'pytest.xml'}"]),
        (["python3", "-m", "pytest"], "pytest", [f"--junitxml={OUT / 'pytest.xml'}"]),
        (["uv", "run", "pytest", "tests"], "pytest", [f"--junitxml={OUT / 'pytest.xml'}"]),
        (["CI=1", "pytest"], "pytest", [f"--junitxml={OUT / 'pytest.xml'}"]),
        (["npx", "jest"], "jest", ["--json", f"--outputFile={OUT / 'jest.json'}"]),
        (["npx", "--no-install", "vitest", "run"], "vitest", None),
        (["go", "test", "./..."], "go", None),
        (
            ["dotnet", "test"],
            "dotnet",
            ["--logger", "trx", "--results-directory", str(OUT / "dotnet")],
        ),
        (["./mvnw", "-B", "test"], "maven", []),
        (["./gradlew", "test"], "gradle", []),
        (["bundle", "exec", "rspec"], "rspec", None),
        (["vendor/bin/phpunit"], "phpunit", ["--log-junit", str(OUT / "phpunit.xml")]),
        (["make", "test"], "", []),
    ],
)
def test_prepare_adds_what_each_runner_needs(
    tmp_path: Path, argv: list[str], runner: str, added: list[str] | None
) -> None:
    wrapped = _prep(argv, tmp_path)
    assert wrapped.runner == runner
    if added is not None:
        assert wrapped.argv == [*argv, *added]


def test_go_test_gets_json_and_is_printed_as_text(tmp_path: Path) -> None:
    wrapped = _prep(["go", "test", "-v", "./..."], tmp_path)
    assert wrapped.argv == ["go", "test", "-json", "-v", "./..."]
    assert wrapped.go_json
    printed: list[str] = []
    wrap_run._echo_go_line('{"Action":"output","Output":"=== RUN TestA\\n"}\n', printed.append)
    wrap_run._echo_go_line('{"Action":"pass","Test":"TestA"}\n', printed.append)
    wrap_run._echo_go_line("build failed\n", printed.append)
    assert printed == ["=== RUN TestA\n", "build failed\n"]


def test_package_scripts_npm_gets_a_separator_pnpm_does_not(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {"scripts": {"test": "jest --ci", "test:ui": "vitest run", "e2e": "playwright test"}}
        )
    )
    npm = _prep(["npm", "test"], tmp_path)
    assert npm.argv == ["npm", "test", "--", "--json", f"--outputFile={OUT / 'jest.json'}"]
    pnpm = _prep(["pnpm", "run", "test:ui"], tmp_path)
    assert pnpm.runner == "vitest"
    assert pnpm.argv[3] == "--reporter=default"
    e2e = _prep(["npm", "run", "e2e"], tmp_path)
    assert e2e.runner == "playwright"
    assert e2e.env == {"PLAYWRIGHT_JSON_OUTPUT_NAME": str(OUT / "playwright.json")}
    assert e2e.tier == "e2e"


def test_a_result_file_the_user_asked_for_is_kept(tmp_path: Path) -> None:
    wrapped = _prep(["pytest", "--junitxml=reports/out.xml"], tmp_path)
    assert wrapped.argv == ["pytest", "--junitxml=reports/out.xml"]
    assert wrapped.globs == ["reports/out.xml"]


def test_a_shell_string_runs_through_sh(tmp_path: Path) -> None:
    wrapped = _prep(["pytest -q && echo ok"], tmp_path)
    assert wrapped.shell
    assert wrapped.argv == ["sh", "-c", "pytest -q && echo ok"]


# --- the command ------------------------------------------------------------------------------


@pytest.fixture
def here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "shop"
    (project / "tests").mkdir(parents=True)
    (project / "tests/test_a.py").write_text(
        "def test_a():\n    pass\n\ndef test_b():\n    assert 0\n"
    )
    monkeypatch.chdir(project)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return project


def _cycles(project: Path) -> list[tuple[str]]:
    with sqlite3.connect(project / "testo_history.db") as db:
        return db.execute("select cycle from runrecord").fetchall()


def test_run_wraps_pytest_records_it_and_keeps_its_exit_code(here: Path) -> None:
    result = CliRunner().invoke(app, ["run", "--", sys.executable, "-m", "pytest", "-q", "tests"])
    assert result.exit_code == 1, result.output  # test_b fails: the command's own exit code
    assert "Recorded run" in result.output
    assert _cycles(here) == [("shop",)]
    assert list((here / ".testo/wrap").glob("*/pytest.xml"))


def test_run_records_files_an_unknown_command_wrote(here: Path) -> None:
    script = here / "make-report.py"
    script.write_text(
        "open('report.xml','w').write('<testsuite name=\"s\" tests=\"1\">"
        '<testcase classname="c" name="t"/></testsuite>\')\n'
    )
    result = CliRunner().invoke(
        app, ["run", "--cycle", "custom", "--", sys.executable, str(script)]
    )
    assert result.exit_code == 0, result.output
    assert _cycles(here) == [("custom",)]


def test_run_without_results_says_so_and_returns_the_command_code(here: Path) -> None:
    result = CliRunner().invoke(app, ["run", "--", sys.executable, "-c", "raise SystemExit(3)"])
    assert result.exit_code == 3
    assert "left no result file" in result.output
    assert not (here / "testo_history.db").exists()


@pytest.mark.skipif(os.name == "nt", reason="a shell script stands in for go")
def test_go_json_is_kept_and_recorded(here: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = here / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "go"
    fake.write_text(
        "#!/bin/sh\n"
        'echo \'{"Action":"run","Package":"x","Test":"TestA"}\'\n'
        'echo \'{"Action":"output","Package":"x","Test":"TestA","Output":"hello from go\\n"}\'\n'
        'echo \'{"Action":"pass","Package":"x","Test":"TestA","Elapsed":0.1}\'\n'
        'echo \'{"Action":"pass","Package":"x","Elapsed":0.1}\'\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = CliRunner().invoke(app, ["run", "--", "go", "test", "./..."])
    assert result.exit_code == 0, result.output
    assert _cycles(here) == [("shop",)]
    (kept,) = (here / ".testo/wrap").glob("*/go-test.json")
    assert '"Action":"pass"' in kept.read_text()


def test_cycle_options_are_refused_with_a_command(here: Path) -> None:
    result = CliRunner().invoke(app, ["run", "--test", "a", "--", "pytest"])
    assert result.exit_code == 2
