"""A large monorepo on GitLab CI with pytest, Behave and BehaveX (closed-network rollout).

Behave/BehaveX detection in ``qasight init --scan``, unique GitLab job names per
folder, result search past many source files, leaving out earlier jobs'
artifacts, and packing a BDD suite's many JUnit files under the upload limit.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import yaml

from qasight.services import ci_templates, project_setup, repo_scan
from qasight.services.ingest_client import combine_junit_files
from qasight.services.junit_ingest import MAX_FILES


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _feature(root: Path, folder: str) -> None:
    _write(root, f"{folder}/a.feature", "Feature: a\n  Scenario: s\n    Given ok\n")
    _write(root, f"{folder}/steps/s.py", "from behave import given\n")


def test_behave_and_behavex_are_found_with_their_features_folder(tmp_path: Path) -> None:
    web, flow, bare = tmp_path / "web", tmp_path / "flow", tmp_path / "bare"
    _write(web, "requirements.txt", "behave==1.2.6\n")
    _feature(web, "features")
    _write(flow, "requirements-test.txt", "behavex\n")
    _feature(flow, "tests/features")
    _feature(bare, "features")  # no dependency file: the step code says behave

    (behave,) = project_setup.detect_project(web).stacks
    assert behave.key == "behave"
    assert behave.stage == {"name": "behave", "equipment": "behave", "args": ["features"]}
    assert behave.install == ("pip install -r requirements.txt",)
    (behavex,) = project_setup.detect_project(flow).stacks
    assert behavex.key == "behavex"
    assert behavex.stage["args"] == ["tests/features"]
    assert behavex.install == ("pip install -r requirements-test.txt",)
    assert [s.key for s in project_setup.detect_project(bare).stacks] == ["behave"]


def test_a_features_folder_without_steps_is_not_behave(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.txt", "behave\n")
    _write(tmp_path, "features/a.feature", "Feature: a\n")
    assert project_setup.detect_project(tmp_path).stacks == ()


def test_behave_ci_commands_write_junit_and_name_the_features(tmp_path: Path) -> None:
    _write(tmp_path, "web/requirements.txt", "behave\n")
    _feature(tmp_path, "web/features")
    _write(tmp_path, "flow/requirements.txt", "behavex\n")
    _feature(tmp_path, "flow/features")
    found = {f.stack.key: f.stack for f in repo_scan.scan_repo(tmp_path).found}
    assert ci_templates.ci_test_command(found["behave"]) == (
        "(cd web && behave features --junit --junit-directory test-results)"
    )
    assert ci_templates.ci_test_command(found["behavex"]) == (
        "(cd flow && mkdir -p test-results && behavex features -o test-results/behavex)"
    )


def test_gitlab_jobs_stay_apart_when_folders_share_a_runner(tmp_path: Path) -> None:
    for name in ("pay", "bill"):
        _write(tmp_path, f"services/{name}/pyproject.toml", "[tool.pytest.ini_options]\n")
        _write(tmp_path, f"services/{name}/tests/test_x.py", "def test_x():\n    pass\n")
    scan = repo_scan.scan_repo(tmp_path)
    jobs = yaml.safe_load(ci_templates.gitlab_ci(scan.facts, cycle="mono"))
    assert set(jobs) == {"stages", "services-bill-pytest", "services-pay-pytest"}
    pay = jobs["services-pay-pytest"]
    assert "qasight ingest --cycle mono-services-pay-pytest" in pay["after_script"]
    assert pay["artifacts"]["paths"] == ["testo-summary.md", "services/pay/test-results/"]
    assert pay["cache"]["key"] == "testo-history-services-pay-pytest-$CI_COMMIT_REF_SLUG"


def test_result_search_is_not_stopped_by_many_source_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(project_setup, "_MAX_WALK_FILES", 50)
    for i in range(200):
        _write(tmp_path, f"aaa/src/m{i}.py", "")
    result = _write(
        tmp_path, "zzz/reports/TESTS-a.xml", '<testsuite name="a"><testcase name="t"/></testsuite>'
    )
    assert project_setup.find_result_files(tmp_path) == [result.resolve()]


def test_files_from_before_the_gitlab_job_started_are_left_out(tmp_path: Path) -> None:
    old = _write(tmp_path, "old.xml")
    new = _write(tmp_path, "new.xml")
    hour_ago = time.time() - 3600
    os.utime(old, (hour_ago, hour_ago))
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))

    kept, earlier = project_setup.written_in_this_job([new, old], {"CI_JOB_STARTED_AT": started})
    assert (kept, earlier) == ([new], [old])
    assert project_setup.written_in_this_job([new, old], {}) == ([new, old], [])
    assert project_setup.written_in_this_job([old], {"CI_JOB_STARTED_AT": "soon"}) == ([old], [])


def test_many_junit_files_are_packed_into_one_upload(tmp_path: Path) -> None:
    files = [
        _write(
            tmp_path,
            f"reports/TESTS-f{i}.xml",
            f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="f{i}" tests="1">'
            f'<testcase classname="f{i}" name="s"/></testsuite>',
        )
        for i in range(MAX_FILES + 50)
    ]
    files.append(
        _write(
            tmp_path,
            "reports/both.xml",
            '<testsuites tests="2"><testsuite name="x"><testcase name="a"/></testsuite>'
            '<testsuite name="y"><testcase name="b"/></testsuite></testsuites>',
        )
    )
    other = _write(tmp_path, "reports/jest.json", '{"numTotalTests": 0}')
    latin = _write(
        tmp_path,
        "reports/latin.xml",
        '<?xml version="1.0" encoding="ISO-8859-1"?><testsuite name="l"/>',
    )
    (tmp_path / "out").mkdir()
    out = combine_junit_files([*files, other, latin], tmp_path / "out")
    assert out[1:] == [other, latin]
    from defusedxml import ElementTree

    root = ElementTree.parse(out[0]).getroot()
    assert root.tag == "testsuites"
    assert len(root.findall("testsuite")) == MAX_FILES + 52


def test_the_team_server_takes_its_ai_model_from_the_environment(monkeypatch) -> None:
    import importlib

    import qasight_api.dependencies as deps

    monkeypatch.setenv("TESTO_AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("TESTO_AI_BASE_URL", "http://llm.example.local:8000/v1")
    monkeypatch.setenv("TESTO_AI_MODEL", "qwen2.5-coder")
    try:
        settings = importlib.reload(deps).get_ai_settings_store().get()
        assert (settings.enabled, settings.provider, settings.model) == (
            True,
            "openai_compatible",
            "qwen2.5-coder",
        )
        assert settings.base_url == "http://llm.example.local:8000/v1"
        assert settings.timeout_s == 60.0
        monkeypatch.setenv("TESTO_AI_PROVIDER", "nope")
        assert importlib.reload(deps).get_ai_settings_store().get().enabled is False
    finally:
        monkeypatch.delenv("TESTO_AI_PROVIDER", raising=False)
        importlib.reload(deps)
