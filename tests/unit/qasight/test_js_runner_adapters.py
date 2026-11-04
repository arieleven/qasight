"""``jest``, ``vitest`` and ``cypress`` frameworks: argv, config defaults, and real stage runs
against a stand-in ``npx`` that writes what each runner's own report writes."""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from qasight.config.loader import load_config
from qasight.config.schema import Stage
from qasight.engine.executor import run_stage
from qasight.frameworks.base import get_adapter
from qasight.frameworks.cypress_adapter import CypressAdapter
from qasight.frameworks.jest_adapter import JestAdapter, VitestAdapter

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

REPO = Path("/work/app")


def _argv(adapter, *args: str, workers: int = 4) -> list[str]:  # type: ignore[no-untyped-def]
    return adapter.build_argv(
        target_repo=REPO, results_dir=Path("r"), stage_args=args, workers=workers
    )


def test_registered() -> None:
    assert isinstance(get_adapter("jest"), JestAdapter)
    assert isinstance(get_adapter("vitest"), VitestAdapter)
    assert isinstance(get_adapter("cypress"), CypressAdapter)


def test_jest_argv_adds_json_report_and_workers() -> None:
    out = REPO.resolve() / "test-results" / "testo-jest.json"
    assert _argv(JestAdapter(), "src/", "-t", "cart") == [
        "npx",
        "jest",
        "src/",
        "-t",
        "cart",
        "--json",
        f"--outputFile={out}",
        "--maxWorkers=4",
    ]


@pytest.mark.parametrize("flag", [["--json"], ["--outputFile=x.json"]])
def test_jest_own_report_wins(flag: list[str]) -> None:
    assert _argv(JestAdapter(), *flag, workers=0) == ["npx", "jest", *flag]


@pytest.mark.parametrize("flag", [["--runInBand"], ["-i"], ["--maxWorkers=2"], ["-w", "2"]])
def test_jest_own_workers_win(flag: list[str]) -> None:
    assert "--maxWorkers=4" not in _argv(JestAdapter(), *flag)


def test_vitest_runs_once_with_json_report() -> None:
    out = REPO.resolve() / "test-results" / "testo-vitest.json"
    expected = [
        "npx",
        "vitest",
        "run",
        "src/cart",
        "--reporter=default",
        "--reporter=json",
        f"--outputFile.json={out}",
        "--maxWorkers=4",
    ]
    assert _argv(VitestAdapter(), "src/cart") == expected
    assert _argv(VitestAdapter(), "run", "src/cart") == expected
    assert _argv(VitestAdapter(), "watch", "src/cart") == expected


def test_vitest_own_reporter_wins() -> None:
    argv = _argv(VitestAdapter(), "--reporter=junit", "--outputFile=junit.xml", workers=0)
    assert argv == ["npx", "vitest", "run", "--reporter=junit", "--outputFile=junit.xml"]


def test_cypress_argv_adds_junit_reporter() -> None:
    folder = REPO.resolve() / "test-results"
    assert _argv(CypressAdapter(), "run", "--spec", "cypress/e2e/login.cy.ts") == [
        "npx",
        "cypress",
        "run",
        "--spec",
        "cypress/e2e/login.cy.ts",
        "--reporter",
        "junit",
        "--reporter-options",
        f"mochaFile={folder}/testo-cypress-[hash].xml,toConsole=false",
    ]
    assert "junit" not in _argv(CypressAdapter(), "--reporter", "spec")[5:]


@pytest.mark.parametrize(
    ("equipment", "tier"), [("jest", "unit"), ("vitest", "unit"), ("cypress", "e2e")]
)
def test_config_defaults(tmp_path: Path, equipment: str, tier: str) -> None:
    config = tmp_path / "qasight.yaml"
    config.write_text(
        f"version: 1\ncycles:\n  web:\n    stages:\n      - {{name: web, equipment: {equipment}}}\n",
        encoding="utf-8",
    )
    (stage,) = load_config(config).cycles["web"].stages
    assert (stage.framework, stage.tier) == (equipment, tier)


_FAKE_NPX = textwrap.dedent(
    """\
    #!{python}
    import json, pathlib, sys
    args = sys.argv[1:]
    print("argv:", " ".join(args))
    if args[0] in ("jest", "vitest"):
        flag = "--outputFile=" if args[0] == "jest" else "--outputFile.json="
        out = pathlib.Path(next(a for a in args if a.startswith(flag)).split("=", 1)[1])
        case = {{"ancestorTitles": ["cart"], "title": "adds", "status": "passed", "duration": 4}}
        if args[0] == "vitest":
            case["meta"] = {{}}
        bad = dict(case, title="refunds", status="failed", failureMessages=["Error: nope"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({{"numTotalTests": 2, "testResults": [
            {{"name": str(pathlib.Path.cwd() / "src" / "cart.test.ts"), "assertionResults": [case, bad]}}
        ]}}))
    else:
        opts = args[args.index("--reporter-options") + 1]
        mocha = opts.split(",")[0].split("=", 1)[1].replace("[hash]", "abc")
        pathlib.Path(mocha).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(mocha).write_text(
            '<testsuites><testsuite name="login"><testcase classname="login" name="works"/>'
            '</testsuite></testsuites>'
        )
    sys.exit(1 if args[0] != "cypress" else 0)
    """
)


@pytest.mark.parametrize(
    ("equipment", "expected"),
    [
        ("jest", {"adds": "passed", "refunds": "failed"}),
        ("vitest", {"adds": "passed", "refunds": "failed"}),
        ("cypress", {"works": "passed"}),
    ],
)
def test_stage_imports_the_runners_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, equipment: str, expected: dict[str, str]
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text(_FAKE_NPX.format(python=sys.executable), encoding="utf-8")
    npx.chmod(npx.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    repo = tmp_path / "repo"
    repo.mkdir()
    stage = Stage(name="web", framework=equipment, target_repo=repo, workers=2, tier="unit")

    result = run_stage(stage, plan_name="app", artifacts_root=tmp_path / "artifacts")

    stage_dir = tmp_path / "artifacts" / "app" / "web"
    log = (stage_dir / "run.log").read_text(encoding="utf-8")
    assert f"imported {len(expected)} test(s) from 1 file(s)" in log
    results = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (stage_dir / "allure-results" / equipment).glob("*-result.json")
    ]
    assert {r["name"]: r["status"] for r in results} == expected
    if equipment != "cypress":
        assert result.returncode == 1
        assert {r["fullName"] for r in results} >= {"src/cart.test.ts › cart › adds"}
        framework = {
            lab["value"] for r in results for lab in r["labels"] if lab["name"] == "framework"
        }
        assert framework == {equipment}
