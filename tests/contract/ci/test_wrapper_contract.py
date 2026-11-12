from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


def _load_wrapper_module():
    script = Path("integrations/github-action/run_testo_action.py").resolve()
    spec = importlib.util.spec_from_file_location("run_testo_action_contract", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_action_contract_has_required_inputs_outputs() -> None:
    payload = yaml.safe_load(
        Path("integrations/github-action/action.yml").read_text(encoding="utf-8")
    )
    assert set(payload["inputs"].keys()) == {
        "results",
        "history",
        "config-path",
        "cycle",
        "ci-mode",
        "persist",
        "python-version",
        "package",
        "job-summary",
        "comment-on-pr",
        "annotations",
        "slack-webhook",
        "teams-webhook",
        "notify-when",
        "flaky-issues",
        "coverage",
        "environment",
        "release",
        "github-token",
        "ai-summary",
        "comment-artifact",
        "post-comments-from",
        "history-artifact",
        "server-url",
        "server-token",
        "static-site",
        "static-site-base-path",
        "scorecard",
        "scorecard-path",
        "scorecard-min-score",
        "scorecard-no-new",
        "scorecard-tools",
    }
    assert set(payload["outputs"].keys()) == {
        "exit_code",
        "summary_json",
        "summary_path",
        "status",
        "site_dir",
        "scorecard_dir",
        "scorecard_sarif",
    }


def test_wrapper_uses_testo_run_ci_command_shape() -> None:
    module = _load_wrapper_module()
    cmd = module.build_command(config_path="config.yml", cycle="", ci_mode=True, persist=True)
    assert cmd[:2] == ["qasight", "run"]
    assert "--ci" in cmd


def test_github_fixture_is_one_line_consumer() -> None:
    workflow = Path("tests/fixtures/ci/github_workflow_minimal.yml").read_text(encoding="utf-8")
    assert "uses: arieleven/qasight@v1" in workflow


def test_scorecard_scan_command_carries_the_gate() -> None:
    module = _load_wrapper_module()
    out = Path("/tmp/out")
    plain = module.build_scorecard_scan_command(
        path="", out=out, min_score="", no_new=False, tools=""
    )
    assert plain == ["qasight", "scorecard", "scan", ".", "--out", "/tmp/out"]
    gated = module.build_scorecard_scan_command(
        path="services/api", out=out, min_score=" 70 ", no_new=True, tools="osv-scanner,zizmor"
    )
    assert gated[3] == "services/api"
    assert gated[-5:] == ["--min-score", "70", "--no-new", "--tools", "osv-scanner,zizmor"]
